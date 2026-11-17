import copy
import json
import unittest
from unittest.mock import patch
from backend.grading import grade_answer, FixtureProvider, ChatCompletionsProvider, ValidationError
from examples.generate_fixture import build_fixture


class SequenceProvider:
    model = 'test-sequence'

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def complete(self, system, payload):
        self.calls.append(payload)
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value)


class GradingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = build_fixture()
        self.q = self.fixture['question']
        self.r = self.fixture['rubric']
        self.a = self.fixture['answer']
        self.output = self.fixture['model_output']

    def grade(self, output=None, **kwargs):
        return grade_answer(self.q, self.r, self.a, provider=FixtureProvider(self.output if output is None else output), **kwargs)

    def test_success_reproducible_and_no_mutation(self):
        before = copy.deepcopy(self.fixture)
        first, second = self.grade(), self.grade()
        self.assertEqual(first, second)
        self.assertEqual(first['suggested_total'], 3)
        self.assertEqual(first['status'], 'SUGGESTED')
        self.assertFalse(first['teacher_confirmed'])
        self.assertEqual(self.fixture, before)

    def test_invalid_outputs_fail_closed(self):
        variants = []
        for key, value in [('suggested_score', -1), ('suggested_score', 3),
                           ('suggested_score', 0.5), ('suggested_score', True),
                           ('suggested_score', float('nan')), ('suggested_score', float('inf')),
                           ('rule_id', 'invented'), ('reason', ''), ('flags', 'review'), ('evidence', [])]:
            bad = copy.deepcopy(self.output)
            bad['items'][0][key] = value
            variants.append(bad)
        for transform in [lambda b: b['items'].pop(),
                          lambda b: b['items'].append(b['items'][0]),
                          lambda b: b['items'][0].update(criterion_id='unknown'),
                          lambda b: b['items'][0]['evidence'][0].update(quote='invented'),
                          lambda b: b['items'][0]['evidence'][0].update(start=-1),
                          lambda b: b['items'][0]['evidence'][0].update(start=True),
                          lambda b: b['items'][0]['evidence'][0].update(end=999),
                          lambda b: b.update(suggested_total=8),
                          lambda b: b.update(review_reasons='wrong')]:
            bad = copy.deepcopy(self.output)
            transform(bad)
            variants.append(bad)
        variants += ['not json', '[]', '{"items":[],"items":[],"review_reasons":[]}']
        for bad in variants:
            with self.subTest(bad=bad):
                provider = SequenceProvider([bad, bad])
                result = grade_answer(self.q, self.r, self.a, provider=provider)
                self.assertEqual(result['status'], 'REVIEW_REQUIRED')
                self.assertIsNone(result['suggested_total'])
                self.assertEqual(result['items'], [])
                self.assertEqual(len(provider.calls), 2)

    def test_repair_is_bounded_and_has_feedback(self):
        provider = SequenceProvider(['broken', self.output])
        result = grade_answer(self.q, self.r, self.a, provider=provider)
        self.assertEqual(result['suggested_total'], 3)
        self.assertEqual(result['metadata']['attempts'], 2)
        self.assertIn('validation_feedback', provider.calls[1])

    def test_transport_failure_is_not_zero_and_no_secret_leak(self):
        for error in [TimeoutError('secret-key'), RuntimeError('HTTP 429 secret-key')]:
            provider = SequenceProvider([error])
            result = grade_answer(self.q, self.r, self.a, provider=provider)
            self.assertEqual(result['status'], 'FAILED')
            self.assertIsNone(result['suggested_total'])
            self.assertNotIn('secret-key', json.dumps(result))
            self.assertEqual(len(provider.calls), 1)

    def test_flags_and_reasons_trigger_review(self):
        self.output['items'][0]['flags'] = ['CONTRADICTION']
        self.output['review_reasons'] = ['UNFAMILIAR_SOLUTION']
        result = self.grade()
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertIn('R1: CONTRADICTION', result['review_reasons'])
        self.assertEqual(result['suggested_total'], 3)

    def test_empty_answer_does_not_call_provider(self):
        self.a['text'] = ' \n'
        result = grade_answer(self.q, self.r, self.a, provider=SequenceProvider([]))
        self.assertEqual(result['review_reasons'], ['EMPTY_ANSWER'])
        self.assertIsNone(result['suggested_total'])
        self.assertEqual(result['metadata']['attempts'], 0)

    def test_reordered_items_are_returned_in_rubric_order(self):
        self.output['items'].reverse()
        self.assertEqual([r['criterion_id'] for r in self.grade()['items']], ['R1', 'R2', 'R3', 'R4'])

    def test_input_validation_before_model_call(self):
        for target, field, value in [('q', 'max_score', 9), ('r', 'status', 'DRAFT'),
                                     ('a', 'question_id', 'Q99'), ('a', 'text', None), ('r', 'version', '')]:
            with self.subTest(field=field):
                self.setUp()
                getattr(self, target)[field] = value
                with self.assertRaises(ValidationError):
                    self.grade()
        self.setUp()
        for field, value in [('allowed_scores', [0, 3]), ('allowed_scores', [0, 1, 1, 2]), ('max_score', -2), ('rules', {})]:
            with self.subTest(field=field):
                self.setUp()
                self.r['criteria'][0][field] = value
                with self.assertRaises(ValidationError):
                    self.grade()

    def test_cases_and_call_budget_rejected(self):
        for kwargs in [{'cases': [{}]}, {'max_attempts': 3}, {'max_attempts': True}]:
            with self.assertRaises(ValidationError):
                grade_answer(self.q, self.r, self.a, provider=FixtureProvider(self.output), **kwargs)

    def test_fractional_scores_sum_without_float_accumulation(self):
        self.q['max_score'] = 0.4
        for c, row in zip(self.r['criteria'], self.output['items']):
            c.update(max_score=0.1, allowed_scores=[0, 0.1])
            row['suggested_score'] = 0.1 if row['evidence'] else 0
        self.assertEqual(self.grade()['suggested_total'], 0.2)

    def test_unicode_offsets_are_exact_and_not_normalized(self):
        self.a['text'] = '😀\r\ne\u0301'
        for row in self.output['items']:
            row['suggested_score'] = 0
            row['evidence'] = []
        self.output['items'][0].update(suggested_score=2, evidence=[{'quote': 'e\u0301', 'start': 3, 'end': 5}])
        self.assertEqual(self.grade()['suggested_total'], 2)
        self.output['items'][0]['evidence'][0]['quote'] = 'é'
        self.assertIsNone(self.grade()['suggested_total'])

    def test_valid_zero_is_a_suggestion_not_a_failure(self):
        for row in self.output['items']:
            row.update(suggested_score=0, evidence=[])
        result = self.grade()
        self.assertEqual(result['status'], 'SUGGESTED')
        self.assertEqual(result['suggested_total'], 0)
        self.assertEqual(len(result['items']), 4)

    def test_input_hash_changes_with_rubric_version(self):
        first = self.grade()['metadata']['input_sha256']
        self.r['version'] = 'synthetic-v2'
        self.assertNotEqual(first, self.grade()['metadata']['input_sha256'])

    def test_http_adapter_request_and_timeout(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'choices': [{'message': {'content': json.dumps(self.output)}}]}).encode()
        with patch('backend.grading.providers.urlopen', return_value=response) as open_mock:
            provider = ChatCompletionsProvider('https://example.invalid/chat/completions', 'model-name', 'fake-key', timeout=17)
            result = grade_answer(self.q, self.r, self.a, provider=provider)
            request = open_mock.call_args.args[0]
            body = json.loads(request.data)
            self.assertEqual(body['temperature'], 0)
            self.assertEqual(open_mock.call_args.kwargs['timeout'], 17)
            self.assertEqual(result['suggested_total'], 3)

    def test_http_adapter_requires_explicit_https_configuration(self):
        with self.assertRaises(ValueError):
            ChatCompletionsProvider('http://example.invalid', 'model', 'key')


if __name__ == '__main__':
    unittest.main()
