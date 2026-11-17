import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.grading import grade_answer, FixtureProvider, ValidationError
from backend.grading.calibration import correction_draft, select_cases, practice_candidates, confirmed_feedback
from backend.grading.datasets import audit_pairs, split_unseen, read_jsonl
from backend.grading.evaluation import evaluate
from backend.grading.baselines import grade_baseline
from examples.generate_fixture import build_fixture
from tests.test_grading import SequenceProvider


class Workflows(unittest.TestCase):
    def setUp(self):
        self.f = build_fixture()
        self.q, self.r, self.a, self.o = [self.f[k] for k in ('question', 'rubric', 'answer', 'model_output')]

    def case(self):
        draft = correction_draft(self.q, self.r, self.a, self.o)
        return dict(draft, teacher_approved=True, case_id='case1', case_set_version='set-v1')

    def target(self):
        return dict(self.a, answer_id='new-answer', text=self.a['text'] + '\n另一份')

    def test_draft_not_approved_and_general_scope_requires_text(self):
        draft = correction_draft(self.q, self.r, self.a, self.o)
        self.assertEqual(draft['status'], 'DRAFT')
        self.assertFalse(draft['teacher_approved'])
        with self.assertRaises(ValidationError):
            select_cases(self.q, self.r, self.target(), [draft])
        with self.assertRaises(ValidationError):
            correction_draft(self.q, self.r, self.a, self.o, scope='general_rule')

    def test_case_version_and_confirmation_guard(self):
        for change in ({'rubric_version': 'old'}, {'question_id': 'other'},
                       {'teacher_approved': False}, {'case_set_version': ''}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                select_cases(self.q, self.r, self.target(), [dict(self.case(), **change)])

    def test_mixed_case_set_and_duplicate_ids_rejected(self):
        first = self.case()
        second = dict(first, case_id='case2', case_set_version='set-v2')
        for cases in ([first, first], [first, second]):
            with self.assertRaises(ValidationError):
                select_cases(self.q, self.r, self.target(), cases)

    def test_self_and_whitespace_duplicate_cannot_retrieve(self):
        for target in (self.a, dict(self.a, answer_id='different', text=' \n' + self.a['text'])):
            self.assertEqual(select_cases(self.q, self.r, target, [self.case()]), [])

    def test_approved_cases_reach_model_with_pinned_version(self):
        p = SequenceProvider([self.o])
        result = grade_answer(self.q, self.r, self.target(), [self.case()], provider=p)
        self.assertEqual(result['case_set_version'], 'set-v1')
        self.assertEqual(result['metadata']['selected_case_ids'], ['case1'])
        self.assertEqual(len(p.calls[0]['reviewed_cases']), 1)
        self.assertNotIn('score', p.calls[0]['answer'])

    def test_explicit_draft_mode_can_never_return_low_risk(self):
        self.r['status'] = 'DRAFT'
        with self.assertRaises(ValidationError):
            grade_answer(self.q, self.r, self.a, provider=FixtureProvider(self.o))
        result = grade_answer(self.q, self.r, self.a, provider=FixtureProvider(self.o), allow_draft=True)
        self.assertEqual(self.r['status'], 'DRAFT')
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertTrue(result['metadata']['experimental_draft'])

    def test_targeted_recheck_disagreement_retained(self):
        first = copy.deepcopy(self.o)
        first['review_reasons'] = ['PROSE_CODE_CONFLICT']
        second = copy.deepcopy(self.o)
        second['items'][0]['suggested_score'] = 0
        p = SequenceProvider([first, second])
        result = grade_answer(self.q, self.r, self.a, provider=p)
        self.assertIn('targeted_review', p.calls[1])
        self.assertIn('RECHECK_SCORE_DISAGREEMENT', result['review_reasons'])
        self.assertEqual(result['metadata']['attempts'], 2)

    def test_targeted_recheck_budget_and_unresolved_flags(self):
        self.o['review_reasons'] = ['UNKNOWN_METHOD']
        p = SequenceProvider([self.o])
        result = grade_answer(self.q, self.r, self.a, provider=p, max_attempts=1)
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertEqual(len(p.calls), 1)

    def test_practice_uses_only_confirmed_scores_and_existing_approved_ids(self):
        confirmed = {'teacher_confirmed': True, 'question_id': 'Q',
                     'confirmed_items': [{'criterion_id': 'R1', 'score': 0, 'max_score': 2}]}
        catalog = [{'practice_id': 'P1', 'teacher_approved': True, 'question_id': 'Q', 'criterion_ids': ['R1']},
                   {'practice_id': 'P2', 'teacher_approved': False, 'question_id': 'Q', 'criterion_ids': ['R1']}]
        self.assertEqual(practice_candidates(confirmed, catalog)[0]['practice_id'], 'P1')
        self.assertEqual(practice_candidates(confirmed, catalog, attempts=['P1']), [])
        with self.assertRaises(ValidationError):
            practice_candidates(dict(confirmed, teacher_confirmed=False), catalog)

    def test_split_excludes_calibration_duplicates_and_keeps_groups_together(self):
        plain = [{'answer_id': str(i), 'q_id': 'Q', 'answer': text}
                 for i, text in enumerate(['甲 乙', '甲乙', '丙', '丁', '丁\n', '戊'])]
        truth = [dict(r, score='3') for r in plain]
        learning = truth[:1]
        audit = audit_pairs(plain, truth, learning)
        self.assertEqual(audit['learning_duplicate_exclusions'], ['1'])
        split = split_unseen(plain, learning)
        ids = [{r['answer_id'] for r in rows} for rows in split.values()]
        self.assertTrue(ids[0].isdisjoint(ids[1]))
        self.assertEqual(set.union(*ids), {'2', '3', '4', '5'})
        self.assertTrue(any({'3', '4'} <= part for part in ids))

    def test_pair_mismatch_and_label_in_unscored_rejected(self):
        plain = [{'answer_id': 'a', 'q_id': 'Q', 'answer': '文本'}]
        for scored in ([dict(plain[0], answer='different', score=1)], [dict(plain[0], score=11)]):
            with self.assertRaises(ValidationError):
                audit_pairs(plain, scored, [])
        with self.assertRaises(ValidationError):
            audit_pairs([dict(plain[0], score=1)], [dict(plain[0], score=1)], [])

    def test_baseline_labels_only_in_examples_not_target(self):
        p = SequenceProvider([{'score': 0, 'reason': '未作答'}])
        question = dict(self.q, reference='参考答案')
        samples = [{'answer_id': self.a['answer_id'], 'q_id': self.q['question_id'], 'answer': self.a['text'], 'score': '8'}]
        target = dict(self.target(), score=8, secret='must not send')
        result = grade_baseline(question, target, provider=p, examples=samples)
        self.assertEqual(result['suggested_total'], 0)
        self.assertEqual(set(p.calls[0]['answer']), {'question_id', 'answer_id', 'text'})
        self.assertEqual(len(p.calls[0]['scored_examples']), 1)

    def test_baseline_self_exclusion_and_invalid_repair(self):
        p = SequenceProvider(['bad', {'score': 1, 'reason': '说明'}])
        samples = [{'answer_id': 'other', 'q_id': self.q['question_id'], 'answer': self.a['text'], 'score': 10}]
        result = grade_baseline(dict(self.q, reference='参考'), self.a, provider=p, examples=samples)
        self.assertEqual(p.calls[0]['scored_examples'], [])
        self.assertEqual(result['metadata']['attempts'], 2)

    def test_evaluation_counts_failed_review_missing_and_zero(self):
        labels = [{'answer_id': str(i), 'score': i} for i in range(4)]
        predictions = [{'answer_id': '0', 'suggested_total': 0, 'status': 'SUGGESTED'},
                       {'answer_id': '1', 'suggested_total': 3, 'status': 'REVIEW_REQUIRED'},
                       {'answer_id': '2', 'suggested_total': None, 'status': 'FAILED'}]
        report = evaluate(predictions, labels)
        self.assertEqual(report['all_model_suggestions']['mae'], 1)
        self.assertEqual(report['low_risk_suggestions']['mae'], 0)
        self.assertEqual(report['review_count'], 1)
        self.assertEqual(report['scored_coverage'], 0.5)
        self.assertEqual(report['missing_ids'], ['3'])
        self.assertEqual(report['failed_ids'], ['2'])

    def test_evaluation_no_silent_alignment_or_invalid_score(self):
        labels = [{'answer_id': 'a', 'score': 0}]
        for rows in ([{'answer_id': 'extra', 'score': 0}],
                     [{'answer_id': 'a', 'score': 0}, {'answer_id': 'a', 'score': 0}],
                     [{'answer_id': 'a', 'score': float('nan')}],
                     [{'answer_id': 'a', 'score': True}]):
            with self.assertRaises(ValidationError):
                evaluate(rows, labels)

    def test_cli_refuses_scored_input_before_api(self):
        from backend.grading.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'q.json').write_text(json.dumps(dict(self.q, reference='参考')))
            (base / 'a.jsonl').write_text(json.dumps({'answer_id': 'a', 'q_id': self.q['question_id'], 'answer': 'abc', 'score': 10}))
            argv = ['cli', 'run', '--question', str(base / 'q.json'), '--answers', str(base / 'a.jsonl'),
                    '--output', str(base / 'out.jsonl'), '--mode', 'direct', '--live']
            with patch('sys.argv', argv), patch('backend.grading.cli.ChatCompletionsProvider.from_env') as provider:
                with self.assertRaises(SystemExit):
                    main()
                provider.assert_not_called()

    def test_confirmed_feedback_rejects_fabricated_quotes(self):
        confirmed = {'teacher_confirmed': True, 'answer_id': self.a['answer_id'],
                     'question_id': self.a['question_id'],
                     'confirmed_items': [{'criterion_id': 'R1', 'score': 0, 'max_score': 2,
                                          'teacher_reason': '本次缺少解释', 'evidence': []}]}
        self.assertEqual(confirmed_feedback(self.a, confirmed)[0]['feedback'], '本次缺少解释')
        confirmed['confirmed_items'][0]['evidence'] = [{'quote': '伪造', 'start': 0, 'end': 2}]
        with self.assertRaises(ValidationError):
            confirmed_feedback(self.a, confirmed)

    def test_cli_runs_real_pipeline_with_mock_service_and_preserves_existing_output(self):
        from backend.grading.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'q.json').write_text(json.dumps(dict(self.q, reference='参考')))
            (base / 'r.json').write_text(json.dumps(self.r))
            (base / 'a.jsonl').write_text(json.dumps({'answer_id': self.a['answer_id'], 'q_id': self.q['question_id'], 'answer': self.a['text']}))
            argv = ['cli', 'run', '--question', str(base / 'q.json'), '--rubric', str(base / 'r.json'),
                    '--answers', str(base / 'a.jsonl'), '--output', str(base / 'out.jsonl'), '--mode', 'structured', '--live']
            with patch('sys.argv', argv), patch('backend.grading.cli.ChatCompletionsProvider.from_env', return_value=FixtureProvider(self.o)):
                main()
                rows = read_jsonl(base / 'out.jsonl')
                self.assertEqual(rows[0]['suggested_total'], 3)
                self.assertEqual(rows[0]['metadata']['mode'], 'structured')
                with self.assertRaises(FileExistsError):
                    main()


if __name__ == '__main__':
    unittest.main()
