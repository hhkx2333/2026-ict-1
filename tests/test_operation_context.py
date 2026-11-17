import copy
import json
import unittest
from backend.grading.checklist import canonicalize, grade_checked_answer, source_units
from backend.grading.operation_context import annotate_operations
from backend.grading.core import ValidationError
from tests import test_checklist
from tests.test_grading import SequenceProvider


class OperationContextTests(unittest.TestCase):
    def setUp(self):
        fixture = test_checklist.ChecklistTests()
        fixture.setUp()
        self.q, self.r, self.a, self.wire = fixture.q, fixture.r, fixture.a, fixture.wire
        self.r['analysis_policy'] = 'operation-context-v1'
        self.a['text'] = '入队：\n    push(A,x);\n出队：\n    pop(B,x);\n判空：\n    empty(A) && empty(B)'
        for row in self.wire['items']:
            for check in row['checks']:
                if check['score']:
                    check['evidence'] = [{'source_id': 'L4'}]
        self.analysis = {'method': 'STANDARD', 'invariant': 'A holds arrivals, B holds reversed output',
            'operations': [{'operation': op, 'assessment': 'SUPPORTED', 'reason': 'synthetic replay only',
                            'evidence': [{'source_id': sid}]} for op, sid in
                           [('enqueue', 'L2'), ('dequeue', 'L4'), ('empty', 'L6')]]}
        self.wire['algorithm_analysis'] = self.analysis

    def test_heading_hints_keep_offsets_and_do_not_infer_from_keywords(self):
        text = '用S1执行入队，S2执行出队\n1、入队：push\n    while(x)\n2、出队：pop\n3、判断队列是否为空：test'
        units = annotate_operations(source_units(text))
        self.assertEqual([u['operation'] for u in units],
                         ['unspecified', 'enqueue', 'enqueue', 'dequeue', 'empty'])
        for u in units:
            self.assertEqual(text[u['start']:u['end']], u['text'])

    def test_transfer_in_wrong_operation_is_rejected_and_repaired(self):
        check = self.wire['items'][0]['checks'][0]
        self.r['criteria'][0]['checks'][0]['evidence_operation'] = 'dequeue'
        bad = copy.deepcopy(self.wire)
        bad['items'][0]['checks'][0]['evidence'] = [{'source_id': 'L2'}]
        provider = SequenceProvider([bad, self.wire])
        result = grade_checked_answer(self.q, self.r, self.a, provider=provider)
        self.assertEqual(result['suggested_total'], 3)
        self.assertIn('another operation', provider.calls[1]['validation_feedback'])
        self.assertEqual(result['metadata']['algorithm_analysis']['method'], 'STANDARD')

    def test_incomplete_operation_cannot_also_get_full_operation_score(self):
        self.r['criteria'][0]['checks'][0]['requires_supported_operation_for_full'] = 'enqueue'
        self.analysis['operations'][0]['assessment'] = 'INCOMPLETE'
        with self.assertRaisesRegex(ValidationError, 'contradict'):
            canonicalize(json.dumps(self.wire), self.r, self.a)
        self.wire['items'][0]['checks'][0]['score'] = 1
        canonicalize(json.dumps(self.wire), self.r, self.a)

    def test_unresolved_analysis_forces_review_even_if_model_omits_risk_flag(self):
        self.analysis['operations'][1]['assessment'] = 'UNCLEAR'
        result = grade_checked_answer(self.q, self.r, self.a,
            provider=SequenceProvider([self.wire, self.wire]))
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertIn('EVIDENCE_UNCERTAIN', result['metadata']['risk_codes'])
        self.assertEqual(result['metadata']['attempts'], 2)

    def test_alternative_can_use_enqueue_invariant_for_equivalent_dequeue_function(self):
        self.r['criteria'][0]['checks'][0]['evidence_operation'] = 'dequeue'
        self.analysis['method'] = 'ALTERNATIVE'
        self.wire['items'][0]['checks'][0]['evidence'] = [{'source_id': 'L2'}]
        raw, _ = canonicalize(json.dumps(self.wire), self.r, self.a)
        self.assertEqual(json.loads(raw)['items'][0]['suggested_score'], 2)
        # This validates routing, not actual correctness of an alternative algorithm.

    def test_missing_duplicate_and_fabricated_analysis_evidence_fail_closed(self):
        for mutation in ('missing', 'duplicate', 'fake', 'no_evidence'):
            wire = copy.deepcopy(self.wire)
            if mutation == 'missing': wire.pop('algorithm_analysis')
            elif mutation == 'duplicate': wire['algorithm_analysis']['operations'][1] = wire['algorithm_analysis']['operations'][0]
            elif mutation == 'fake': wire['algorithm_analysis']['operations'][0]['evidence'] = [{'source_id': 'L999'}]
            else: wire['algorithm_analysis']['operations'][0]['evidence'] = []
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                canonicalize(json.dumps(wire), self.r, self.a)

    def test_transport_key_is_rejected_with_specific_repair_feedback(self):
        bad = copy.deepcopy(self.wire)
        bad['type'] = 'json_object'
        p = SequenceProvider([bad, self.wire])
        result = grade_checked_answer(self.q, self.r, self.a, provider=p)
        self.assertEqual(result['suggested_total'], 3)
        self.assertIn('unexpected: type', p.calls[1]['validation_feedback'])
        self.assertIn('algorithm_analysis, items, review_reasons', p.calls[1]['validation_feedback'])

    def test_full_multi_check_operation_cannot_contradict_incomplete_analysis(self):
        first=self.r['criteria'][0]['checks'][0]
        second=self.r['criteria'][1]['checks'][0]
        self.r['operation_bundles']={'dequeue':[first['check_id'],second['check_id']]}
        self.wire['items'][1]['checks'][0]['score']=second['max_score']
        self.analysis['operations'][1]['assessment']='INCOMPLETE'
        with self.assertRaisesRegex(ValidationError,'bundle contradicts'):
            canonicalize(json.dumps(self.wire),self.r,self.a)
        self.wire['items'][1]['checks'][0]['score']=0
        canonicalize(json.dumps(self.wire),self.r,self.a)

    def test_nested_review_reasons_are_rejected_with_exact_repair_feedback(self):
        bad=copy.deepcopy(self.wire)
        bad['algorithm_analysis']['review_reasons']=[]
        p=SequenceProvider([bad,self.wire])
        result=grade_checked_answer(self.q,self.r,self.a,provider=p)
        self.assertEqual(result['suggested_total'],3)
        self.assertIn('unexpected: review_reasons',p.calls[1]['validation_feedback'])
