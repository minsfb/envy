"""First export registration must bind the generated frozen graph projection."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import test_research_graph_exports as fixture
import test_research_graph_withdrawal_projections as withdrawals

GRAPH, EXPORTS, BUNDLE = fixture.GRAPH, fixture.EXPORTS, fixture.BUNDLE


class ExportProjectionTests(unittest.TestCase):
    setUp = fixture.ResearchGraphExportsTests.setUp
    operation = fixture.ResearchGraphExportsTests.operation
    returned = fixture.ResearchGraphExportsTests.returned
    packet = fixture.ResearchGraphExportsTests.packet
    rows = fixture.ResearchGraphExportsTests.rows
    prepare = withdrawals.WithdrawalProjectionTests.prepare
    settle_scopes = fixture.ResearchGraphExportsTests.settle_scopes
    semantic_review = fixture.ResearchGraphExportsTests.semantic_review

    def interrupted(self):
        self.prepare()
        self.settle_scopes()
        with patch.object(GRAPH, 'register_export', side_effect=RuntimeError('owned interruption')):
            with self.assertRaisesRegex(RuntimeError, 'owned interruption'):
                EXPORTS.export_snapshot(self.root, 'interrupted')
        self.directory = self.root / 'graph/exports/interrupted'
        self.relative = str((self.directory / 'manifest.json').relative_to(self.root))
        self.original = {name: (self.directory / name).read_bytes() for name in ('manifest.json', 'ledger.json')}
        self.history = self.rows('graph_events')

    def edit_ledger(self, field):
        ledger = GRAPH._read_json(self.original['ledger.json'])
        if field == 'claim':
            ledger['claims'][0]['text'] = 'A fictional conclusion absent from the accepted graph.'
        else:
            ledger['sources'][0]['finding'] = 'A fictional finding absent from the accepted assertions.'
        data = GRAPH._bytes(ledger)
        (self.directory / 'ledger.json').write_bytes(data)
        manifest = GRAPH._read_json(self.original['manifest.json'])
        next(row for row in manifest['files'] if row['relative_path'] == 'ledger.json').update(
            sha256=GRAPH._sha(data), size_bytes=len(data))
        (self.directory / 'manifest.json').write_bytes(GRAPH._bytes(manifest))
        return GRAPH._sha((self.directory / 'manifest.json').read_bytes())

    def test_interrupted_retry_rejects_edited_ledger_with_recomputed_manifest(self):
        self.interrupted()
        for field in ('claim', 'finding'):
            with self.subTest(field=field):
                self.edit_ledger(field)
                retained = {name: (self.directory / name).read_bytes() for name in self.original}
                with self.assertRaisesRegex(GRAPH.GraphError, 'generated frozen graph projection'):
                    EXPORTS.export_snapshot(self.root, 'interrupted')
                self.assertEqual(self.rows('export_manifests'), [])
                self.assertEqual(self.rows('graph_events'), self.history)
                self.assertEqual(retained, {name: (self.directory / name).read_bytes() for name in self.original})
                self.assertEqual(GRAPH.validate(self.root)['status'], 'pass')

    def test_raw_controller_registration_rejects_edit_under_alternate_module_identity(self):
        self.interrupted()
        digest = self.edit_ledger('claim')
        path = Path(GRAPH.__file__)
        spec = importlib.util.spec_from_file_location('isolated_projection_controller', path)
        isolated = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isolated)
        for controller in (GRAPH, isolated):
            with self.subTest(controller=controller.__name__):
                with self.assertRaises(controller.GraphError) as caught:
                    controller.register_export(self.root, 'interrupted', self.relative, digest)
                self.assertEqual(caught.exception.code, 'integrity_error')
                self.assertIn('generated frozen graph projection', str(caught.exception))
                self.assertEqual(self.rows('graph_events'), self.history)
        self.assertEqual(self.rows('export_manifests'), [])

    def test_valid_raw_registration_uses_frozen_projection_and_retains_original_bytes(self):
        self.interrupted()
        claim = GRAPH._decoded(self.rows('claims')[0])
        self.packet('later-claim', [fixture.WORK._update('claims', claim, status='challenged', reason='New uncertainty')], kind='reconcile')
        digest = GRAPH._sha(self.original['manifest.json'])
        receipt = GRAPH.register_export(self.root, 'interrupted', self.relative, digest)
        self.assertEqual(receipt['manifest_sha256'], digest)
        self.assertEqual(self.original, {name: (self.directory / name).read_bytes() for name in self.original})
        ledger = BUNDLE.read(self.directory / 'ledger.json')
        context = EXPORTS.validate_ledger(self.root, ledger)
        self.assertTrue(context['stale'])
        self.assertEqual([row['id'] for row in ledger['claims']], ['claim'])
        self.assertEqual(EXPORTS.verify_export(self.directory)['status'], 'pass')

    def test_bound_pending_and_registered_snapshots_keep_original_renderer_bytes(self):
        self.interrupted()
        def interrupt(stage):
            if stage == 'after_packet_fsync':
                raise RuntimeError('bound pending packet')
        with patch.object(GRAPH, '_fault', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'bound pending packet'):
                EXPORTS.export_snapshot(self.root, 'interrupted')
        pending = self.root / 'graph/updates' / ('export-' + GRAPH._sha(b'interrupted') + '.json')
        pending_bytes = pending.read_bytes()
        with patch.object(EXPORTS, '_verify_generated_ledger', side_effect=AssertionError('renderer changed')):
            receipt = EXPORTS.export_snapshot(self.root, 'interrupted')
            self.assertFalse(receipt['idempotent'])
            self.assertTrue(EXPORTS.export_snapshot(self.root, 'interrupted')['idempotent'])
            ledger = BUNDLE.read(self.directory / 'ledger.json')
            context = EXPORTS.validate_ledger(self.root, ledger)
            self.assertEqual(BUNDLE.validate(self.root, ledger, 'synthesis', semantic_review=self.semantic_review(context))['claims'], 1)
        self.assertEqual(pending.read_bytes(), pending_bytes)
        self.assertEqual(self.original, {name: (self.directory / name).read_bytes() for name in self.original})
        self.assertEqual(GRAPH.validate(self.root)['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
