"""Offline synthetic B-to-C contract demonstration, not real teacher or model evidence."""
import copy
import json
from .generate_fixture import build_fixture
from backend.grading import (FixtureProvider, grade_submission, case_from_confirmed,
                            feedback_and_practice, correction_draft)


def run_demo():
    f=build_fixture()
    q,r,a,o=[f[k] for k in ('question','rubric','answer','model_output')]
    initial=grade_submission({'question':q,'rubric':r,'answer':a,'mode':'structured'},provider=FixtureProvider(o))
    corrected=copy.deepcopy(o)
    draft=correction_draft(q,r,a,corrected,scope='answer_only')
    confirmed={'question_id':q['question_id'],'answer_id':a['answer_id'],'rubric_version':r['version'],
        'teacher_confirmed':True,'approved_for_reuse':True,'review_id':'SYNTHETIC-REVIEW',
        'reviewer_id':'synthetic-teacher-not-a-real-approval','confirmed_at':'2026-11-15T10:00:00+08:00',
        'confirmed_items':[{'criterion_id':item['criterion_id'],'score':item['suggested_score'],
            'max_score':c['max_score'],'teacher_reason':item['reason'],'evidence':item['evidence']}
            for c,item in zip(r['criteria'],corrected['items'])]}
    case=case_from_confirmed(q,r,a,confirmed,case_id='synthetic-case1',case_set_version='synthetic-set1')
    target=dict(a,answer_id='synthetic-next-answer',text=a['text']+'\n额外表述')
    next_result=grade_submission({'question':q,'rubric':r,'answer':target,'cases':[case],'mode':'structured'},
                                  provider=FixtureProvider(o))
    catalog=[{'practice_id':'synthetic-exercise1','question_id':'synthetic-new-question',
              'source_question_id':q['question_id'],'criterion_ids':[r['criteria'][1]['criterion_id']],
              'teacher_approved':True}]
    learning=feedback_and_practice(q,r,a,confirmed,catalog)
    return {'notice':'全部为合成回放；没有真实教师批准、没有线上模型请求，不证明校准效果。',
            'initial_result':initial,'correction_draft':draft,'reusable_case':case,
            'next_result':next_result,'feedback_and_practice':learning}

if __name__=='__main__':print(json.dumps(run_demo(),ensure_ascii=False,indent=2))
