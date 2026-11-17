"""Version-bound teacher-approved cases, draft corrections and lexical retrieval."""
import copy
import hashlib
import json
import re
from .core import ValidationError, validate_input, validate_output, _require, _text


def text_key(text):
    return re.sub(r'\s+', '', text).casefold()


def _tokens(text):
    text = text_key(text)
    return set(text[i:i + 2] for i in range(max(0, len(text) - 1))) | set(re.findall(r'[a-z0-9_]+', text))


def select_cases(question, rubric, answer, cases, limit=3):
    if type(limit) is not int or not 0 <= limit <= 5:
        raise ValidationError('case limit must be 0..5')
    if not isinstance(cases, list):
        raise ValidationError('cases must be a list')
    ranked, seen, versions = [], set(), set()
    query = _tokens(answer['text'])
    for case in cases:
        if not isinstance(case, dict) or case.get('teacher_approved') is not True:
            raise ValidationError('only explicitly teacher-approved cases may be retrieved')
        if case.get('question_id') != question['question_id'] or case.get('rubric_version') != rubric['version']:
            raise ValidationError('case question/rubric version mismatch')
        if not isinstance(case.get('case_id'), str) or not case['case_id'] or case['case_id'] in seen:
            raise ValidationError('invalid or duplicate case_id')
        seen.add(case['case_id'])
        version = case.get('case_set_version')
        if not isinstance(version, str) or not version.strip():
            raise ValidationError('missing case_set_version')
        versions.add(version)
        ca = case.get('answer')
        validate_input(question, rubric, ca)
        validate_output(json.dumps(case.get('assessment'), ensure_ascii=False), rubric, ca)
        # A target answer and its exact/whitespace duplicate must never retrieve itself.
        if ca['answer_id'] == answer['answer_id'] or text_key(ca['text']) == text_key(answer['text']):
            continue
        tokens = _tokens(ca['text'])
        similarity = len(query & tokens) / max(1, len(query | tokens))
        if similarity == 0:
            continue
        ranked.append((similarity, case['case_id'], case))
    if len(versions) > 1:
        raise ValidationError('mixed case set versions')
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return copy.deepcopy([item[2] for item in ranked[:limit]])


def correction_draft(question, rubric, answer, corrected_assessment, *, scope='answer_only', proposed_rule=None, allow_draft=False):
    """A draft is never automatically published or treated as an approved case."""
    _require(type(allow_draft) is bool, 'allow_draft must be boolean')
    draft = isinstance(rubric,dict) and rubric.get('status') == 'DRAFT'
    validation_rubric = dict(rubric,status='PUBLISHED') if draft and allow_draft else rubric
    validate_input(question, validation_rubric, answer)
    validate_output(json.dumps(corrected_assessment, ensure_ascii=False), rubric, answer)
    if scope not in ('answer_only', 'general_rule'):
        raise ValidationError('unknown correction scope')
    if scope == 'general_rule' and (not isinstance(proposed_rule, str) or not proposed_rule.strip()):
        raise ValidationError('general rule draft needs teacher-reviewable text')
    content = dict(question_id=question['question_id'], rubric_version=rubric['version'],
                   answer=answer, assessment=corrected_assessment, scope=scope,
                   proposed_rule=proposed_rule, teacher_approved=False, status='DRAFT',
                   source_rubric_status=rubric['status'])
    content['draft_id'] = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return copy.deepcopy(content)


def practice_candidates(confirmed, catalog, attempts=()):
    """Recommend existing approved exercises from teacher-confirmed criterion errors."""
    if not isinstance(confirmed, dict) or confirmed.get('teacher_confirmed') is not True:
        raise ValidationError('feedback requires a teacher-confirmed result')
    if not isinstance(confirmed.get('confirmed_items'), list):
        raise ValidationError('confirmed_items are required; model suggestions are insufficient')
    from .datasets import score_number
    _require(isinstance(catalog, (list,tuple)) and isinstance(attempts, (list,tuple,set))
             and all(_text(a) for a in attempts), 'catalog and attempts must be collections')
    missed, criteria = set(), set()
    for row in confirmed['confirmed_items']:
        _require(isinstance(row, dict), 'invalid confirmed criterion')
        cid = row.get('criterion_id')
        if not isinstance(cid, str) or not cid or cid in criteria:
            raise ValidationError('invalid confirmed criterion')
        criteria.add(cid)
        maximum = score_number(row.get('max_score'), 1000000)
        if maximum <= 0:
            raise ValidationError('confirmed criterion maximum must be positive')
        if score_number(row.get('score'), maximum) < maximum:
            missed.add(cid)
    result, seen = [], set()
    for item in catalog:
        _require(isinstance(item, dict), 'invalid exercise')
        _require(isinstance(item.get('criterion_ids'), list) and all(_text(c) for c in item['criterion_ids']),
                 'exercise criterion_ids must be a list of strings')
        pid = item.get('practice_id')
        if not isinstance(pid, str) or not pid or pid in seen:
            raise ValidationError('practice_id must be unique')
        seen.add(pid)
        if item.get('teacher_approved') is not True or pid in attempts:
            continue
        if item.get('source_question_id', item.get('question_id')) != confirmed.get('question_id'):
            continue
        matched = sorted(missed & set(item.get('criterion_ids', [])))
        if matched:
            result.append({'practice_id': pid, 'criterion_ids': matched,
                           'reason': '针对本次教师确认的失分点练习；不代表长期掌握情况。'})
    return result[:3]


def confirmed_feedback(answer, confirmed):
    """Render teacher-confirmed observations, without inventing semantic diagnoses."""
    if not isinstance(confirmed, dict) or confirmed.get('teacher_confirmed') is not True or confirmed.get('answer_id') != answer.get('answer_id') or confirmed.get('question_id') != answer.get('question_id'):
        raise ValidationError('feedback needs matching, teacher-confirmed answer')
    from .datasets import score_number
    rows = confirmed.get('confirmed_items')
    if not isinstance(rows, list):
        raise ValidationError('confirmed_items required')
    feedback, seen = [], set()
    for item in rows:
        _require(isinstance(item, dict), 'invalid confirmed criterion')
        cid = item.get('criterion_id')
        if not isinstance(cid, str) or not cid or cid in seen:
            raise ValidationError('invalid confirmed criterion')
        seen.add(cid)
        maximum = score_number(item.get('max_score'), 1000000)
        score = score_number(item.get('score'), maximum)
        note = item.get('teacher_reason')
        if maximum <= 0 or not isinstance(note, str) or not note.strip():
            raise ValidationError('teacher reason and positive maximum required')
        evidence = item.get('evidence', [])
        if not isinstance(evidence, list):
            raise ValidationError('evidence must be a list')
        for span in evidence:
            if not isinstance(span, dict) or type(span.get('start')) is not int or type(span.get('end')) is not int:
                raise ValidationError('invalid confirmed evidence')
            if not 0 <= span['start'] < span['end'] <= len(answer['text']) or answer['text'][span['start']:span['end']] != span.get('quote'):
                raise ValidationError('confirmed quote mismatch')
        if score < maximum:
            feedback.append({'criterion_id': cid, 'score': score, 'max_score': maximum,
                             'evidence': copy.deepcopy(evidence), 'feedback': note,
                             'scope': '仅描述本次作答，不推定长期知识掌握程度。'})
    return feedback
