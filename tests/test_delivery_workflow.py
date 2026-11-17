import copy
import json
import unittest
from backend.grading import (FixtureProvider, ValidationError, grade_submission, case_from_confirmed,
                            feedback_and_practice, MeteredProvider)
from examples.generate_fixture import build_fixture
from tests.test_grading import SequenceProvider


class DeliveryWorkflowTests(unittest.TestCase):
    def setUp(self):
        f=build_fixture()
        self.q,self.r,self.a,self.o=[f[k] for k in ('question','rubric','answer','model_output')]
        self.confirmed={'teacher_confirmed':True,'question_id':self.q['question_id'],
            'answer_id':self.a['answer_id'],'rubric_version':self.r['version'],
            'approved_for_reuse':True,'review_id':'SYNTHETIC-REVIEW','reviewer_id':'synthetic-teacher',
            'confirmed_at':'2026-11-15T10:00:00+08:00',
            'confirmed_items':[{'criterion_id':row['criterion_id'],'max_score':c['max_score'],
                'score':row['suggested_score'],'teacher_reason':row['reason'],'evidence':row['evidence']}
                for row,c in zip(self.o['items'],self.r['criteria'])]}

    def test_confirm_reuse_grade_feedback_and_new_exercise_flow(self):
        case=case_from_confirmed(self.q,self.r,self.a,self.confirmed,case_id='C1',case_set_version='set1')
        target=dict(self.a,answer_id='synthetic-second',text=self.a['text']+'\nother')
        p=SequenceProvider([self.o])
        result=grade_submission({'question':self.q,'rubric':self.r,'answer':target,
                                  'cases':[case],'mode':'structured'},provider=p)
        self.assertEqual(result['metadata']['selected_case_ids'],['C1'])
        self.assertFalse(result['teacher_confirmed'])
        self.assertEqual(result['metadata']['usage_summary']['model_calls'],1)
        c=self.confirmed
        catalog=[{'practice_id':'P1','source_question_id':self.q['question_id'],
                  'question_id':'new-question','criterion_ids':[self.r['criteria'][1]['criterion_id']],
                  'teacher_approved':True}]
        output=feedback_and_practice(self.q,self.r,self.a,c,catalog)
        self.assertTrue(output['feedback'])
        self.assertEqual(output['practice_candidates'][0]['practice_id'],'P1')
        self.assertEqual(feedback_and_practice(self.q,self.r,self.a,c,catalog,['P1'])['practice_candidates'],[])

    def test_review_provenance_version_scope_and_reuse_guards(self):
        for changes in ({'approved_for_reuse':False},{'teacher_confirmed':False},
                        {'rubric_version':'old'},{'reviewer_id':''},{'answer_id':'other'},
                        {'confirmed_items':[]}):
            with self.subTest(changes=changes),self.assertRaises(ValidationError):
                case_from_confirmed(self.q,self.r,self.a,dict(self.confirmed,**changes),case_id='C',case_set_version='v1')
        rubric=dict(self.r,status='DRAFT')
        with self.assertRaises(ValidationError):
            case_from_confirmed(self.q,rubric,self.a,self.confirmed,case_id='C',case_set_version='v1')

    def test_total_only_or_false_evidence_cannot_become_a_case(self):
        for mode in ('total_only','maximum','fake_quote'):
            c=copy.deepcopy(self.confirmed)
            if mode=='total_only':c.pop('confirmed_items');c['score']=3
            elif mode=='maximum':c['confirmed_items'][0]['max_score']=100
            else:c['confirmed_items'][0]['evidence']=[{'quote':'fiction','start':0,'end':7}]
            with self.subTest(mode=mode),self.assertRaises(ValidationError):
                case_from_confirmed(self.q,self.r,self.a,c,case_id='C',case_set_version='v1')

    def test_worker_refuses_target_label_before_call(self):
        p=SequenceProvider([])
        with self.assertRaises(ValidationError):
            grade_submission({'question':self.q,'rubric':self.r,'answer':dict(self.a,score=3),
                              'mode':'structured'},provider=p)
        self.assertEqual(p.calls,[])

    def test_nested_metering_counts_usage_once_and_scrubs_exception_message(self):
        class Provider:
            model='synthetic'
            last_usage={'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}
            last_response={'model':'actual-revision','id':'response-1','api_key':'do-not-log'}
            def complete(self,system,payload):return 'not json'
        outer=MeteredProvider(Provider(),max_calls=2)
        inner=MeteredProvider(outer,max_calls=2)
        inner.complete('sys',{})
        self.assertEqual(inner.summary()['usage_totals']['total_tokens'],15)
        self.assertEqual(outer.summary()['usage_totals']['total_tokens'],15)
        self.assertNotIn('api_key',str(outer.calls))
        class Failed:
            model='synthetic'
            def complete(self,*args):raise RuntimeError('secret-message')
        m=MeteredProvider(Failed(),max_calls=1)
        with self.assertRaises(RuntimeError):m.complete('s',{})
        self.assertNotIn('secret-message',str(m.calls))
        self.assertEqual(m.summary()['usage_reported_calls'],0)

    def test_first_valid_assessment_is_preserved_when_review_output_fails(self):
        first=copy.deepcopy(self.o);first['review_reasons']=['unclear']
        p=SequenceProvider([first,'invalid'])
        result=grade_submission({'question':self.q,'rubric':self.r,'answer':self.a,
                                  'mode':'structured'},provider=p)
        self.assertIsNone(result['suggested_total'])
        self.assertEqual(result['status'],'REVIEW_REQUIRED')
        self.assertEqual(result['metadata']['assessment_history'][0]['suggested_total'],3)

    def test_truncated_response_is_not_a_valid_completion_and_usage_is_recorded(self):
        from unittest.mock import patch
        from backend.grading.providers import ChatCompletionsProvider
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return json.dumps({'id':'r1','model':'actual-model',
                'usage':{'total_tokens':20},'choices':[{'finish_reason':'length',
                'message':{'content':'{"items":['}}]}).encode()
        p=ChatCompletionsProvider('https://example.test/chat/completions','m','synthetic-key')
        meter=MeteredProvider(p,max_calls=1)
        with patch('backend.grading.providers.urlopen',return_value=Response()):
            with self.assertRaisesRegex(ValidationError,'MODEL_OUTPUT_TRUNCATED'):
                meter.complete('s',{})
        self.assertEqual(meter.summary()['usage_totals']['total_tokens'],20)
        self.assertEqual(meter.calls[0]['response']['finish_reason'],'length')
        self.assertNotIn('synthetic-key',str(meter.calls))

    def test_http_failure_records_only_safe_diagnostics_and_clears_previous_usage(self):
        import io
        from urllib.error import HTTPError
        from unittest.mock import patch
        from backend.grading.providers import ChatCompletionsProvider
        p=ChatCompletionsProvider('https://example.test/chat/completions','m','synthetic-key')
        p.last_usage={'total_tokens':999}
        body=io.BytesIO(json.dumps({'error':{'code':'invalid_request_error','type':'invalid_request_error',
            'message':'DO_NOT_LOG_SENSITIVE_BODY'}}).encode())
        error=HTTPError(p.endpoint,400,'bad',{},body)
        meter=MeteredProvider(p,max_calls=1)
        with patch('backend.grading.providers.urlopen',side_effect=error):
            with self.assertRaises(HTTPError):meter.complete('JSON',{})
        error.close()
        self.assertEqual(meter.calls[0]['response']['http_status'],400)
        self.assertEqual(meter.calls[0]['usage'],{})
        self.assertNotIn('DO_NOT_LOG_SENSITIVE_BODY',str(meter.calls))

    def test_correction_under_draft_rules_stays_unapproved(self):
        from backend.grading import correction_draft
        rubric=dict(self.r,status='DRAFT')
        with self.assertRaises(ValidationError):correction_draft(self.q,rubric,self.a,self.o)
        draft=correction_draft(self.q,rubric,self.a,self.o,allow_draft=True)
        self.assertEqual(draft['status'],'DRAFT')
        self.assertEqual(draft['source_rubric_status'],'DRAFT')
        self.assertFalse(draft['teacher_approved'])

    def test_practice_suitability_unknown_until_exact_recommendation_review(self):
        from backend.grading.feedback_quality import practice_review_tasks,evaluate_practice_quality
        rows=[{'answer_id':'a','question_id':'Q','rubric_version':'v1',
               'practice_candidates':[{'practice_id':'P','criterion_ids':['R'],'reason':'synthetic'}]}]
        self.assertIsNone(evaluate_practice_quality(rows,[])['appropriate_rate_all'])
        task=practice_review_tasks(rows)[0]
        reviewed=dict(task,teacher_confirmed=True,reviewer_id='teacher',verdict='APPROPRIATE')
        self.assertEqual(evaluate_practice_quality(rows,[reviewed])['appropriate_rate_all'],1)
        altered=copy.deepcopy(rows);altered[0]['practice_candidates'][0]['reason']='different'
        with self.assertRaises(ValidationError):evaluate_practice_quality(altered,[reviewed])
