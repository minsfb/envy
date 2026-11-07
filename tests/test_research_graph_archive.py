"""Validate the fictional provider independently of any agent's interpretations."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("graph_archive_fixture", ROOT / "research_graph_archive.py")
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
CORPUS = ROOT / "fixtures/retrospective-research/graph-agent"


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="envy-research-archive-")
        self.addCleanup(directory.cleanup)
        self.state_path = Path(directory.name) / "state.json"
        self.provider = archive.Archive(CORPUS, self.state_path, "fresh", "fixture-fresh")

    def test_pages_preserve_every_result_and_bind_query(self):
        request = {"route": "archive.title", "query": "Tern Queue", "page_size": 5}
        first = self.provider.search(request)
        self.assertEqual(first["pagination"], "more")
        ids, page = list(first["source_ids"]), first
        while page["next_cursor"] is not None:
            page = self.provider.search(dict(request, cursor=page["next_cursor"]))
            ids.extend(page["source_ids"])
        self.assertEqual(len(ids), first["total_matches"])
        self.assertEqual(len(ids), len(set(ids)))
        with self.assertRaises(archive.ArchiveError) as error:
            self.provider.search(dict(request, query="different", cursor=first["next_cursor"]))
        self.assertEqual(error.exception.code, "invalid_cursor")

    def test_exclusions_apply_before_search_counts_and_direct_reads(self):
        result = self.provider.search({"route": "archive.title", "query": "Spring queue study"})
        self.assertEqual(result["total_matches"], 0)
        self.assertEqual(result["sources"], [])
        status, _, content = self.provider.read({"artifact_id": "doc-021"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(content)["status"], "out_of_scope")
        with self.assertRaises(archive.ArchiveError):
            archive.Archive(CORPUS, self.state_path, "reuse", "fixture-fresh")

    def test_failure_has_no_body_and_retry_is_shared_across_threads_and_restart(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: self.provider.read({"artifact_id": "doc-011"}), range(2)))
        self.assertEqual(sorted(r[0] for r in results), [200, 503])
        failed = json.loads(next(r[2] for r in results if r[0] == 503))
        self.assertTrue(failed["retryable"])
        self.assertNotIn("content", failed)
        self.assertNotIn("sha256", failed)
        resumed = archive.Archive(CORPUS, self.state_path, "fresh", "fixture-fresh")
        self.assertEqual(resumed.read({"artifact_id": "doc-011"})[0], 200)
        self.assertEqual(resumed.state["attempts"]["doc-011:1"], 3)

    def test_access_failure_and_metadata_never_expose_restricted_body(self):
        for _ in range(2):
            status, _, content = self.provider.read({"artifact_id": "doc-017"})
            self.assertEqual(status, 403)
            result = json.loads(content)
            self.assertEqual(result["status"], "access_blocked")
            self.assertFalse(result["retryable"])
            self.assertNotIn("content", result)
            self.assertNotIn("sha256", result)
        metadata = self.provider.search({"route": "archive.body", "query": "fault-camera"})
        restricted = next(source for source in metadata["sources"] if source["source_id"] == "doc-017")
        self.assertEqual(restricted["snippet"], "Abrupt-power campaign notebook; collection access restricted.")

    def test_same_title_sources_stay_distinct_and_read_returns_exact_bytes(self):
        result = self.provider.search({"route": "archive.title", "query": "Tern Queue pilot review"})
        self.assertEqual({s["external_id"] for s in result["sources"]}, {"doc-004", "doc-018"})
        status, media, content = self.provider.read({"artifact_id": "doc-004", "revision": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(media, "text/plain; charset=utf-8")
        self.assertEqual(content, (CORPUS / "documents/doc-004.txt").read_bytes())

    def test_invalid_route_fields_and_revision_fail_without_content(self):
        with self.assertRaises(archive.ArchiveError):
            self.provider.search({"route": "archive.body", "query": "pilot", "oracle": True})
        with self.assertRaises(archive.ArchiveError):
            self.provider.search({"route": "archive.body", "query": "pilot", "page_size": True})
        result = json.loads(self.provider.read({"artifact_id": "doc-004", "revision": "99"})[2])
        self.assertEqual(result["status"], "revision_unavailable")
        self.assertNotIn("content", result)

    def test_corrupt_fixture_never_records_a_successful_read(self):
        copied = self.state_path.parent / "corpus"
        shutil.copytree(CORPUS, copied)
        provider = archive.Archive(copied, self.state_path, "fresh", "fixture-fresh")
        (copied / "documents/doc-004.txt").write_text("Changed owned test fixture")
        status, media, content = provider.read({"artifact_id": "doc-004"})
        self.assertNotEqual(status, 200)
        self.assertEqual(media, "application/json")
        self.assertEqual(json.loads(content)["status"], "fixture_corrupt")
        self.assertEqual(json.loads(self.state_path.read_text())["events"][-1]["status"], "fixture_corrupt")
        self.assertNotIn(b"Changed owned", content)

    def test_http_adapter_returns_plain_source_bytes_and_failure_metadata(self):
        server = archive.serve(self.provider)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            def post(route, value):
                request = Request("http://127.0.0.1:" + str(server.server_port) + route,
                                  data=json.dumps(value).encode(), headers={"Content-Type": "application/json"})
                try:
                    response = urlopen(request, timeout=3)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, response.headers["Content-Type"], response.read()
            status, _, body = post("/capabilities", {})
            self.assertEqual(status, 200)
            capabilities = json.loads(body)
            self.assertEqual(capabilities["maximum_page_size"], 5)
            seeds_route = capabilities["transport"]["seeds"]
            seeds = json.loads(post(seeds_route["path"], seeds_route["request"])[2])
            search = capabilities["search_request"]
            first = json.loads(post(search["path"], search["example"])[2])
            self.assertEqual(first["pagination"], "more")
            source_id = seeds["sources"][0]["source_id"]
            read = capabilities["read_request"]
            status, media, body = post(read["path"], {read["required"][0]: source_id})
            self.assertEqual((status, media), (200, "text/plain; charset=utf-8"))
            self.assertEqual(body, (CORPUS / "documents" / (source_id + ".txt")).read_bytes())
            status, media, body = post("/read", {"artifact_id": "doc-017"})
            self.assertEqual((status, media), (403, "application/json"))
            self.assertEqual(json.loads(body)["status"], "access_blocked")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
