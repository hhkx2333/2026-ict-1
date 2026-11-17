"""Integration boundary for C's authenticated worker; no web server or persistence."""
import copy
import json
import time
from .core import ValidationError, _require, _text, validate_input, validate_output, grade_answer
from .checklist import grade_checked_answer
from .calibration import correction_draft, confirmed_feedback, practice_candidates
from .telemetry import MeteredProvider


def grade_submission(request, *, provider):
    """C supplies server-loaded records, never browser-provided approval assertions."""
    required = {'question', 'rubric', 'answer'}
    optional = {'cases', 'mode', 'max_attempts', 'allow_draft'}
    _require(isinstance(request, dict) and required <= set(request) <= required | optional,
             'grading request has missing or unexpected fields')
    req = copy.deepcopy(request)
    answer = req['answer']
    _require(isinstance(answer, dict) and set(answer) == {'question_id', 'answer_id', 'text'},
             'answer must contain only question_id/answer_id/text; score labels are forbidden')
    mode = req.get('mode', 'checklist')
    _require(mode in ('structured', 'checklist'), 'unsupported grading mode')
    attempts = req.get('max_attempts', 2)
    _require(type(attempts) is int and 1 <= attempts <= 2, 'max_attempts must be 1 or 2')
    _require(isinstance(req.get('cases', []), list), 'cases must be a list')
    tracked = MeteredProvider(provider, max_calls=attempts)
    tick = time.monotonic()
    grader = grade_checked_answer if mode == 'checklist' else grade_answer
    result = grader(req['question'], req['rubric'], answer, req.get('cases', []), provider=tracked,
                    max_attempts=attempts, allow_draft=req.get('allow_draft', False))
    result['metadata'].update(mode=mode, elapsed_seconds=round(time.monotonic()-tick, 6),
                              model_calls=copy.deepcopy(tracked.calls), usage_summary=tracked.summary())
    return result


def confirmed_assessment(question, rubric, answer, confirmed):
    """Validate teacher records against the actual pinned rubric, including quotes."""
    validate_input(question, rubric, answer)
    _require(isinstance(confirmed, dict) and confirmed.get('teacher_confirmed') is True,
             'teacher-confirmed record required')
    for key in ('question_id', 'answer_id'):
        _require(confirmed.get(key) == answer[key], 'confirmed answer identity mismatch')
    _require(confirmed.get('rubric_version') == rubric['version'], 'confirmed rubric version mismatch')
    rows = confirmed.get('confirmed_items')
    _require(isinstance(rows, list), 'confirmed_items required')
    criteria = {c['criterion_id']: c for c in rubric['criteria']}
    items = []
    for row in rows:
        _require(isinstance(row, dict), 'invalid confirmed item')
        cid = row.get('criterion_id')
        _require(isinstance(cid, str) and cid in criteria, 'unknown confirmed criterion')
        c = criteria[cid]
        _require(type(row.get('max_score')) in (int, float) and row['max_score'] == c['max_score'],
                 'confirmed maximum differs from rubric')
        rule_id = row.get('rule_id')
        if rule_id is None and len(c['rules']) == 1:
            rule_id = next(iter(c['rules']))
        items.append({'criterion_id': cid, 'suggested_score': row.get('score'),
                      'rule_id': rule_id, 'reason': row.get('teacher_reason'),
                      'evidence': copy.deepcopy(row.get('evidence', [])), 'flags': []})
    assessment = {'items': items, 'review_reasons': []}
    validate_output(json.dumps(assessment, ensure_ascii=False, allow_nan=False), rubric, answer)
    return assessment


def case_from_confirmed(question, rubric, answer, confirmed, *, case_id, case_set_version):
    """Convert only explicit reusable teacher approval, not a total-label revision."""
    assessment = confirmed_assessment(question, rubric, answer, confirmed)
    _require(confirmed.get('approved_for_reuse') is True, 'separate approval for case reuse required')
    _require(all(_text(confirmed.get(k)) for k in ('review_id', 'reviewer_id', 'confirmed_at')),
             'review provenance required; C must authenticate reviewer identity')
    _require(_text(case_id) and _text(case_set_version), 'case ID and case-set version required')
    return {'case_id': case_id, 'case_set_version': case_set_version,
            'question_id': question['question_id'], 'rubric_version': rubric['version'],
            'teacher_approved': True, 'answer': copy.deepcopy(answer), 'assessment': assessment,
            'approval': {k: confirmed[k] for k in ('review_id', 'reviewer_id', 'confirmed_at')}}


def feedback_and_practice(question, rubric, answer, confirmed, catalog, attempts=()):
    confirmed_assessment(question, rubric, answer, confirmed)
    return {'question_id': answer['question_id'], 'answer_id': answer['answer_id'],
            'rubric_version': rubric['version'], 'teacher_confirmed': True,
            'feedback': confirmed_feedback(answer, confirmed),
            'practice_candidates': practice_candidates(confirmed, catalog, attempts)}
