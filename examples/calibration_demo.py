"""Offline synthetic teacher-confirmation workflow; no semantic model evaluation."""
import copy
import json
from backend.grading import grade_answer, FixtureProvider
from backend.grading.calibration import correction_draft, practice_candidates
from examples.generate_fixture import build_fixture


def main():
    f = build_fixture()
    draft = correction_draft(f['question'], f['rubric'], f['answer'], f['model_output'])
    # Simulate an explicit teacher approval in synthetic data only.
    case = dict(draft, case_id='synthetic-case-1', case_set_version='synthetic-cases-v1',
                teacher_approved=True, status='APPROVED')
    target = copy.deepcopy(f['answer'])
    target['answer_id'] = 'synthetic-unseen'
    target['text'] += '\n这是另一份合成作答。'
    result = grade_answer(f['question'], f['rubric'], target, [case], provider=FixtureProvider(f['model_output']))
    confirmed = {'question_id': f['question']['question_id'], 'teacher_confirmed': True,
                 'confirmed_items': [{'criterion_id': 'R1', 'score': 1, 'max_score': 2}]}
    practice = practice_candidates(confirmed, [{'practice_id': 'synthetic-P1',
        'question_id': f['question']['question_id'], 'criterion_ids': ['R1'], 'teacher_approved': True}])
    print(json.dumps({'notice': '仅回放合成输出，不是模型效果或真实教师审核记录。',
                      'draft_status': draft['status'], 'result': result, 'practice_candidates': practice}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
