"""Human suitability review for existing exercise recommendations, no learning-effect claims."""
import hashlib
import json
from .core import _require, _text


def recommendation_fingerprint(result):
    value={k:result.get(k) for k in ('question_id','answer_id','rubric_version','practice_candidates')}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def practice_review_tasks(results):
    tasks=[]
    for result in results:
        _require(isinstance(result,dict) and _text(result.get('answer_id')) and
                 isinstance(result.get('practice_candidates'),list), 'invalid recommendation result')
        seen=set()
        for row in result['practice_candidates']:
            pid=row.get('practice_id')
            _require(_text(pid) and pid not in seen, 'duplicate or invalid recommended exercise')
            seen.add(pid)
            tasks.append({'answer_id':result['answer_id'],'practice_id':pid,
                          'recommendation_sha256':recommendation_fingerprint(result),
                          'teacher_confirmed':False,'reviewer_id':None,'verdict':None})
    _require(len({(r['answer_id'],r['practice_id']) for r in tasks})==len(tasks),'duplicate recommendation records')
    return tasks


def evaluate_practice_quality(results,reviews):
    tasks=practice_review_tasks(results)
    expected={(r['answer_id'],r['practice_id']):r for r in tasks}
    seen=set();accepted=uncertain=0
    for row in reviews:
        _require(isinstance(row,dict) and _text(row.get('answer_id')) and _text(row.get('practice_id')),
                 'invalid recommendation review')
        key=(row['answer_id'],row['practice_id'])
        _require(key in expected and key not in seen,'unknown or duplicate recommendation review')
        seen.add(key)
        _require(row.get('teacher_confirmed') is True and _text(row.get('reviewer_id')),
                 'exercise suitability requires explicit human review')
        _require(row.get('recommendation_sha256')==expected[key]['recommendation_sha256'],
                 'review belongs to another recommendation')
        _require(row.get('verdict') in ('APPROPRIATE','INAPPROPRIATE','UNCERTAIN'),'invalid suitability verdict')
        accepted+=row['verdict']=='APPROPRIATE';uncertain+=row['verdict']=='UNCERTAIN'
    return {'recommended_count':len(tasks),'reviewed_count':len(seen),'appropriate_count':accepted,
            'uncertain_count':uncertain,'review_coverage':len(seen)/len(tasks) if tasks else None,
            'appropriate_rate_among_reviewed':accepted/len(seen) if seen else None,
            'appropriate_rate_all':accepted/len(tasks) if tasks and len(seen)==len(tasks) else None,
            'note':'Teacher suitability only; not evidence of improved learning or long-term mastery.'}
