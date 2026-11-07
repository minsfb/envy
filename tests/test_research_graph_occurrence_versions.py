"""Every search occurrence/version survives, while captures stay unambiguous."""

import hashlib
import unittest

import test_research_graph_ingest as fixture

INGEST, GRAPH, BUNDLE = fixture.INGEST, fixture.GRAPH, fixture.BUNDLE


class OccurrenceVersionTests(unittest.TestCase):
    def setUp(self):
        self.c = fixture.ResearchGraphIngestTests()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)

    def sources(self, versions, source="history-hit"):
        return [{"source_id": source, "namespace": "fixture-doc", "external_id": "history" if source == "history-hit" else "A",
                 "title": "Fictional historical document", "version": version} for version in versions]

    def test_search_retains_all_distinct_and_unknown_versions(self):
        c = self.c
        for key, versions in (("same", [{"revision": "v1"}, {"revision": "v1"}]),
                              ("distinct", [{"revision": "v1"}, {"revision": "v2"}]), ("unknown", [{}, {}])):
            with self.subTest(versions=key):
                sources = self.sources(versions)
                c.operation(key, query=True, sources=sources, preflight=True)
                path = c.root / "operations" / key / "result.json"
                original = path.read_bytes()
                result = INGEST.index_operation(c.root, key)
                self.assertEqual(result["state"], "complete")
                occurrences = [r for r in c.rows("discoveries") if r["operation_id"] == key]
                self.assertEqual([r["rank"] for r in occurrences], [0, 1])
                self.assertEqual(len({r["artifact_id"] for r in occurrences}), 1)
                artifact = occurrences[0]["artifact_id"]
                observations = [r for r in c.rows("artifact_versions") if r["artifact_id"] == artifact]
                if key == "same":
                    self.assertEqual([r["provider_revision"] for r in observations], ["v1"])
                elif key == "distinct":
                    self.assertEqual({r["provider_revision"] for r in observations}, {"v1", "v2"})
                else:
                    self.assertEqual(len([r for r in observations if r["provider_revision"] is None]), 2)
                self.assertEqual(BUNDLE.read(path)["sources"], sources)
                self.assertEqual(INGEST.index_operation(c.root, key)["state"], "complete")
                self.assertEqual(path.read_bytes(), original)

    def test_actual_capture_still_rejects_ambiguous_supported_versions_and_omission(self):
        c = self.c
        for key, versions in (("known", [{"revision": "v1"}, {"revision": "v2"}]), ("unknown", [{}, {}])):
            with self.subTest(versions=key):
                if key == "unknown":
                    c.doCleanups()
                    self.setUp()
                    c = self.c
                c.operation(key, sources=self.sources(versions, "doc-a"))
                path = c.root / "operations" / key / "result.json"
                original = path.read_bytes()
                with self.assertRaisesRegex(GRAPH.GraphError, "ambiguous captured source versions"):
                    INGEST.index_operation(c.root, key)
                self.assertFalse((c.root / INGEST._normalization_path("operation", key)).exists())
                with self.assertRaisesRegex(GRAPH.GraphError, "supported source version cannot be omitted"):
                    INGEST.omit_index_version(c.root, key, expected_original_sha256=hashlib.sha256(original).hexdigest(),
                                              omissions=[{"source_index": 1, "source_id": "doc-a", "fields": ["version"]}],
                                              reason="Attempt to remove a supported fictional observation")
                self.assertEqual(path.read_bytes(), original)
        self.assertEqual(c.rows("captures"), [])


if __name__ == "__main__":
    unittest.main()
