"""Fictional recorder regressions for typed claim evidence and reinspection."""

import copy
import json
import unittest

import test_research_graph_exports as fixture

GRAPH, INGEST, EXPORTS, WORK, BUNDLE = fixture.GRAPH, fixture.INGEST, fixture.EXPORTS, fixture.WORK, fixture.BUNDLE


class TypedClaimFixtures:
    setUp = fixture.ResearchGraphExportsTests.setUp
    operation = fixture.ResearchGraphExportsTests.operation
    candidate = fixture.ResearchGraphExportsTests.candidate
    packet = fixture.ResearchGraphExportsTests.packet
    rows = fixture.ResearchGraphExportsTests.rows
    exported_context = fixture.ResearchGraphExportsTests.exported_context
    settle_scopes = fixture.ResearchGraphExportsTests.settle_scopes
    semantic_review = fixture.ResearchGraphExportsTests.semantic_review

    def record_return(self, identity, extent, position, kind, finding=True):
        draft = self.candidate(identity)
        draft['leads'] = []
        draft['coverage'] = 'Exactly these fictional positions: ' + json.dumps(extent, sort_keys=True)
        draft['expansion_review'] = 'No additional leads in this isolated fixture.'
        draft['graph']['scans'] = []
        if not finding:
            draft['graph']['assertions'] = []
        else:
            draft['graph']['assertions'][0]['value'] = {'text': 'The inspected component records a fictional observation.'}
        draft['graph']['locators'][0].update(kind=kind, position=position)
        draft['graph']['locators'][0].pop('excerpt', None)
        inspected = draft['graph']['inspections'][0]
        inspected.update(extent=draft['coverage'], limitations='Only the typed extent was inspected.')
        inspected['captures'][0]['extent'] = extent
        inspected['scopes'][0].update(coverage='partial', basis='Exact fixture positions only.')
        INGEST.validate_reader_return(self.root, draft)
        BUNDLE.budget.reader_return(self.root, draft)
        INGEST.ingest_reader_return(self.root, identity)

    def claim_evidence(self, locator, revision=1, assertion=None):
        return [INGEST._insert('claim_evidence', evidence_id='claim-evidence-' + str(revision),
                    claim_id='claim', claim_revision=revision, locator_id=locator, assertion_id=assertion,
                    stance='support', rationale='Direct fictional locator'),
                INGEST._insert('claim_questions', claim_id='claim', claim_revision=revision,
                    question_id='decision', relationship='answers')]

    def withdraw(self, inspection):
        basis = self.root / 'reviews/basis.txt'
        basis.parent.mkdir()
        basis.write_text('Withdraw the original overstated reading.\n')
        self.packet('withdraw', [INGEST._insert('inspection_withdrawals', withdrawal_id='withdraw',
            inspection_id=inspection, actor_id='coordinator', reason='Original scope was overstated',
            evidence_path='reviews/basis.txt', evidence_sha256=GRAPH._sha(basis.read_bytes()),
            withdrawn_at=GRAPH._now())], kind='reconcile')

    def reinspection(self, original, position, reread, restored):
        captured = {'body': {'unit': 'line', 'ranges': [{'start': 1, 'end': 4}]},
                    'comments': copy.deepcopy(original['comments'])}
        if 'ids' in captured['comments']:
            captured['comments']['ids'] = sorted(set(captured['comments']['ids'] + reread.get('comments', {}).get('ids', [])))
        self.operation(mutate_result=lambda result: result['captures'][0].update(extent=captured))
        INGEST.index_operation(self.root, 'read-one')
        self.record_return('reader-one', original, position, 'comment', finding=False)
        inspection = self.rows('inspections')[0]['inspection_id']
        locator = self.rows('locators')[0]['locator_id']
        self.packet('claim', [INGEST._insert('claims', claim_id='claim', text='The comment supports the fictional conclusion.',
            basis='documented', status='accepted', reason='Original comment reading')] + self.claim_evidence(locator), kind='reconcile')
        old, _ = self.exported_context('original')
        self.assertEqual([c['id'] for c in old['claims']], ['claim'])
        self.withdraw(inspection)
        withdrawn, _ = self.exported_context('withdrawn')
        self.assertEqual(withdrawn['claims'], [])
        component = reread.get('body', reread.get('comments'))
        new_position = {'unit': component['unit']}
        if 'ranges' in component:
            new_position.update(component['ranges'][0])
        if 'ids' in component:
            new_position['id'] = component['ids'][0]
        self.record_return('reread', reread, new_position, 'line' if 'body' in reread else 'comment')
        assertion = self.rows('assertions')[0]['assertion_id']
        self.packet('accept-new', [INGEST._insert('assertion_decisions', decision_id='accept-new', assertion_id=assertion,
            disposition='accepted', reason='Accept only the new component finding.', actor_id='coordinator', expected_revision=0)], kind='reconcile')
        before, context = self.exported_context('before-revision')
        self.assertEqual(before['claims'], [])
        self.assertEqual(context['stop_checks']['withdrawn_evidence_accepted_claim_ids'], ['claim'])
        claim = GRAPH._decoded(self.rows('claims')[0])
        revision = [WORK._update('claims', claim, status='accepted', reason='Revalidate inspected evidence')] + self.claim_evidence(locator, 2)
        if restored:
            self.packet('revise', revision, kind='reconcile')
        else:
            with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
                self.packet('revise', revision, kind='reconcile')
            self.assertEqual(self.rows('claims')[0]['revision'], 1)
        self.settle_scopes()
        ledger, context = self.exported_context('after-reread')
        self.assertEqual([c['id'] for c in ledger['claims']], ['claim'] if restored else [])
        self.assertEqual(context['stop_checks']['withdrawn_evidence_accepted_claim_ids'], [] if restored else ['claim'])
        if restored:
            BUNDLE.validate(self.root, ledger, 'synthesis', semantic_review=self.semantic_review(context))
        else:
            with self.assertRaisesRegex(ValueError, 'unresolved'):
                BUNDLE.validate(self.root, ledger, 'synthesis', semantic_review=self.semantic_review(context))
        self.assertEqual(GRAPH.validate(self.root)['status'], 'pass')
        self.assertEqual(len(self.rows('inspection_withdrawals')), 1)
        self.assertEqual(len(self.rows('inspections')), 2)
        self.assertEqual(EXPORTS.validate_ledger(self.root, old)['status'], 'pass')


class TypedClaimTests(TypedClaimFixtures, unittest.TestCase):
    def test_body_reread_does_not_restore_comment_locator(self):
        ranges = {'unit': 'line', 'ranges': [{'start': 1, 'end': 4}]}
        self.reinspection({'comments': ranges}, {'unit': 'line', 'start': 1, 'end': 4}, {'body': ranges}, False)

    def test_matching_id_only_comment_reread_restores_revised_claim(self):
        extent = {'comments': {'unit': 'comment', 'ids': ['comment-A']}}
        self.reinspection(extent, {'unit': 'comment', 'id': 'comment-A'}, extent, True)

    def test_other_comment_with_same_range_does_not_restore_claim(self):
        ranges = {'unit': 'line', 'ranges': [{'start': 1, 'end': 4}]}
        self.reinspection({'comments': dict(ranges, ids=['comment-A'])},
            {'unit': 'line', 'id': 'comment-A', 'start': 1, 'end': 4},
            {'comments': dict(ranges, ids=['comment-B'])}, False)


class ClaimAcceptanceTests(TypedClaimFixtures, unittest.TestCase):

    def prepare(self, captured_end=4):
        self.operation(mutate_result=lambda result: result['captures'][0]['extent']['body']['ranges'][0].update(end=captured_end))
        INGEST.index_operation(self.root, 'read-one')
        self.record_return('reader-one', {'body': {'unit': 'line', 'ranges': [{'start': 1, 'end': 4}]}},
                           {'unit': 'line', 'start': 1, 'end': 4}, 'line')
        self.assertion = self.rows('assertions')[0]['assertion_id']
        self.locator = self.rows('locators')[0]['locator_id']
        self.capture = self.rows('captures')[0]['capture_id']

    def claim(self, locator=None, assertion=None):
        return [INGEST._insert('claims', claim_id='claim', text='A fictional inspected conclusion.',
            basis='documented', status='accepted', reason='Coordinator review')] + self.claim_evidence(locator or self.locator, assertion=assertion)

    def accept_assertion(self):
        return INGEST._insert('assertion_decisions', decision_id='accept-finding', assertion_id=self.assertion,
            disposition='accepted', reason='Accepted fictional finding.', actor_id='coordinator', expected_revision=0)

    def test_unrelated_and_draft_packets_do_not_rebuild_claim_evidence(self):
        from unittest.mock import patch
        self.prepare()
        with patch.object(GRAPH, 'inspected_claim_evidence', wraps=GRAPH.inspected_claim_evidence) as inspected:
            self.packet('ordinary', [self.accept_assertion()], kind='reconcile')
            self.packet('draft', [INGEST._insert('claims', claim_id='draft', text='A draft conclusion.',
                basis='documented', status='draft', reason='Still under review')], kind='reconcile')
            inspected.assert_not_called()
            self.packet('accepted', self.claim(), kind='reconcile')
            self.assertEqual(inspected.call_count, 1)

    def test_raw_accepted_claim_outside_capture_rolls_back_completed_packet(self):
        self.prepare()
        before = self.rows('graph_events')
        rows = [self.accept_assertion(), INGEST._insert('locators', locator_id='outside', capture_id=self.capture,
                locator_kind='line', locator_json={'unit': 'line', 'start': 100, 'end': 100})] + self.claim('outside')
        with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
            self.packet('outside-capture', rows, kind='reconcile', validator=INGEST.evidence_validator)
        self.assertEqual(self.rows('graph_events'), before)
        self.assertEqual(self.rows('claims'), [])
        self.assertEqual(self.rows('assertion_decisions'), [])

    def test_captured_but_uninspected_locator_cannot_borrow_same_read(self):
        self.prepare(captured_end=100)
        rows = [INGEST._insert('locators', locator_id='outside', capture_id=self.capture,
                locator_kind='line', locator_json={'unit': 'line', 'start': 100, 'end': 100})] + self.claim('outside')
        with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
            self.packet('outside-inspection', rows, kind='reconcile')
        self.assertEqual(self.rows('claims'), [])

    def test_assertion_acceptance_at_packet_end_supports_earlier_claim(self):
        self.prepare()
        self.packet('accepted', self.claim(assertion=self.assertion) + [self.accept_assertion()], kind='reconcile')
        self.settle_scopes()
        ledger, context = self.exported_context('accepted')
        self.assertEqual([c['id'] for c in ledger['claims']], ['claim'])
        self.assertEqual(BUNDLE.validate(self.root, ledger, 'synthesis', semantic_review=self.semantic_review(context))['claims'], 1)

    def test_supplied_assertion_must_be_accepted_and_match_locator(self):
        self.prepare()
        with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
            self.packet('unaccepted-assertion', self.claim(assertion=self.assertion), kind='reconcile')
        rows = [self.accept_assertion(), INGEST._insert('locators', locator_id='different', capture_id=self.capture,
                locator_kind='line', locator_json={'unit': 'line', 'start': 2, 'end': 2})] + self.claim('different', self.assertion)
        with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
            self.packet('different-locator', rows, kind='reconcile')
        self.assertEqual(self.rows('claims'), [])

    def test_latest_assertion_decision_controls_claim_support(self):
        self.prepare()
        self.packet('decisions', [self.accept_assertion(), INGEST._insert('assertion_decisions',
            decision_id='challenge-finding', assertion_id=self.assertion, disposition='challenged',
            reason='Contrary evidence requires review.', actor_id='coordinator', expected_revision=1)], kind='reconcile')
        with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
            self.packet('challenged-assertion', self.claim(assertion=self.assertion), kind='reconcile')

    def test_opaque_locator_requires_matching_accepted_reader_attestation(self):
        self.operation()
        INGEST.index_operation(self.root, 'read-one')
        self.record_return('reader-one', {'body': {'unit': 'line', 'ranges': [{'start': 1, 'end': 4}]}},
                           {'heading': 'Fictional pause'}, 'section')
        self.assertion = self.rows('assertions')[0]['assertion_id']
        self.locator = self.rows('locators')[0]['locator_id']
        with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
            self.packet('opaque-unaccepted', self.claim(), kind='reconcile')
        self.packet('opaque-accepted', self.claim() + [self.accept_assertion()], kind='reconcile')
        ledger, _ = self.exported_context('opaque')
        self.assertEqual([c['id'] for c in ledger['claims']], ['claim'])

    def test_fresh_reading_does_not_restore_withdrawn_assertion_support(self):
        self.prepare()
        self.packet('original', self.claim(assertion=self.assertion) + [self.accept_assertion()], kind='reconcile')
        self.withdraw(self.rows('inspections')[0]['inspection_id'])
        self.record_return('reread', {'body': {'unit': 'line', 'ranges': [{'start': 1, 'end': 4}]}},
                           {'unit': 'line', 'start': 1, 'end': 4}, 'line')
        claim = GRAPH._decoded(self.rows('claims')[0])
        with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
            self.packet('withdrawn-assertion', [WORK._update('claims', claim, reason='Rechecked')]
                + self.claim_evidence(self.locator, 2, self.assertion), kind='reconcile')
        self.assertEqual(self.rows('claims')[0]['revision'], 1)

    def test_accepted_documented_claim_requires_support_in_its_revision(self):
        self.prepare()
        with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
            self.packet('no-evidence', self.claim()[:1], kind='reconcile')
        rows = self.claim()
        rows[1]['record']['stance'] = 'conflict'
        with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
            self.packet('conflict-only', rows, kind='reconcile')
        self.packet('original', self.claim(), kind='reconcile')
        claim = GRAPH._decoded(self.rows('claims')[0])
        with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
            self.packet('revision-without-evidence', [WORK._update('claims', claim, text='A different claim.')], kind='reconcile')
        self.assertEqual(self.rows('claims')[0]['revision'], 1)

    def test_same_packet_withdrawal_prevents_claim_acceptance_in_both_orders(self):
        self.prepare()
        inspection = self.rows('inspections')[0]['inspection_id']
        basis = self.root / 'basis.txt'
        basis.write_text('The original reading was overstated.')
        withdrawal = INGEST._insert('inspection_withdrawals', withdrawal_id='withdraw', inspection_id=inspection,
            actor_id='coordinator', reason='Correct the scope', evidence_path='basis.txt',
            evidence_sha256=GRAPH._sha(basis.read_bytes()), withdrawn_at=GRAPH._now())
        for name, rows in [('first', [withdrawal] + self.claim()), ('last', self.claim() + [withdrawal])]:
            with self.subTest(order=name):
                with self.assertRaisesRegex(GRAPH.GraphError, 'current inspected support'):
                    self.packet('withdraw-' + name, rows, kind='reconcile')
                self.assertEqual(self.rows('inspection_withdrawals'), [])
                self.assertEqual(self.rows('claims'), [])

    def test_historical_unsupported_claim_is_preserved_without_projection_credit(self):
        from unittest.mock import patch
        self.prepare()
        # Simulate a packet already accepted by the old missing claim gate.
        rows = [INGEST._insert('locators', locator_id='outside', capture_id=self.capture,
                locator_kind='line', locator_json={'unit': 'line', 'start': 100, 'end': 100})] + self.claim('outside')
        with patch.object(GRAPH, '_claim_postconditions', return_value=None):
            self.packet('historical', rows, kind='reconcile')
        original = self.rows('claims')
        ledger, _ = self.exported_context('historical-withheld')
        self.assertEqual(ledger['claims'], [])
        self.assertEqual(self.rows('claims'), original)
        self.assertEqual(GRAPH.validate(self.root)['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
