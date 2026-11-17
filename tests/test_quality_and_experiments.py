import copy
import json
import tempfile
import unittest
from pathlib import Path
from backend.grading.core import ValidationError
from backend.grading.quality import evaluate_quality, evidence_tasks, compare_predictions
from backend.grading.experiments import freeze_plan, run_plan, provider_config
from examples.generate_fixture import build_fixture


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.p=[{'question_id':'Q','answer_id':'a','rubric_version':'v1','suggested_total':2,'status':'REVIEW_REQUIRED',
            'items':[{'criterion_id':'R','suggested_score':2,'reason':'test','evidence':[]}],
            'metadata':{'risk_codes':[]}}]
        self.truth=[{'answer_id':'a','score':5}]

    def test_semantic_support_unknown_until_human_audits_exact_prediction(self):
        q=evaluate_quality(self.p,self.truth)
        self.assertIsNone(q['evidence_quality']['support_rate_among_reviewed'])
        task=evidence_tasks(self.p)[0]
        reviewed=dict(task,teacher_confirmed=True,reviewer_id='synthetic-reviewer',verdict='SUPPORTED')
        q=evaluate_quality(self.p,self.truth,evidence_reviews=[reviewed])
        self.assertEqual(q['evidence_quality']['support_rate_among_reviewed'],1)
        changed=copy.deepcopy(self.p);changed[0]['items'][0]['reason']='different assessment'
        with self.assertRaises(ValidationError):evaluate_quality(changed,self.truth,evidence_reviews=[reviewed])
        with self.assertRaises(ValidationError):evaluate_quality(self.p,self.truth,evidence_reviews=[task])

    def test_severe_diagnostic_does_not_truncate_to_top_fifteen(self):
        p=[dict(self.p[0],answer_id=str(i)) for i in range(20)]
        t=[{'answer_id':str(i),'score':8} for i in range(20)]
        q=evaluate_quality(p,t)
        self.assertEqual(len(q['semantic_flag_diagnostic']['severe_without_semantic_flag_ids']),20)
        self.assertEqual(q['low_risk_coverage'],0)

    def test_missing_criterion_truth_and_time_stay_unknown(self):
        q=evaluate_quality(self.p,self.truth)
        self.assertIsNone(q['criterion_quality']['mae'])
        self.assertIsNone(q['human_time']['assisted_total_seconds'])
        t=[{'answer_id':'a','score':5,'teacher_confirmed':True,
            'confirmed_items':[{'criterion_id':'R','score':1,'max_score':2}]}]
        logs=[{'event_id':'1','reviewer_id':'teacher','phase':'review','seconds':12},
              {'event_id':'2','reviewer_id':'teacher','phase':'manual_baseline','seconds':30}]
        q=evaluate_quality(self.p,t,time_logs=logs)
        self.assertEqual(q['criterion_quality']['mae'],1)
        self.assertEqual(q['human_time']['assisted_total_seconds'],12)
        with self.assertRaises(ValidationError):evaluate_quality(self.p,t,time_logs=logs+logs)

    def test_paired_comparison_preserves_failed_coverage(self):
        failed=[dict(self.p[0],status='FAILED',suggested_total=None)]
        report=compare_predictions(self.p,failed,self.truth)
        self.assertEqual(report['joint_valid_count'],0)
        self.assertIsNone(report['mae_change_on_joint_valid'])
        self.assertEqual(report['all_reference_metrics']['right']['failed_ids'],['a'])
        with self.assertRaises(ValidationError):compare_predictions(self.p,[],self.truth)


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        f=build_fixture();self.q=dict(f['question'],reference='synthetic reference')
        self.r,self.a,self.o=f['rubric'],f['answer'],f['model_output']
        wire={'items':[],'review_reasons':[]}
        for c,row in zip(self.r['criteria'],self.o['items']):
            c['checks']=[{'check_id':c['criterion_id']+'-a','description':c['description'],
                         'max_score':c['max_score'],'allowed_scores':c['allowed_scores']}]
            wire['items'].append({'criterion_id':c['criterion_id'],'checks':[
                {'check_id':c['criterion_id']+'-a','score':row['suggested_score'],
                 'reason':row['reason'],'flags':[],
                 'evidence':[{'quote':e['quote']} for e in row['evidence']]}]})
        class Provider:
            endpoint='https://example.test/chat/completions';model='synthetic';max_tokens=512;thinking=None
            last_usage={'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}
            calls=0
            def complete(inner,system,payload):
                inner.calls+=1
                assert 'score' not in payload['answer']
                if 'json' not in system.lower():raise RuntimeError('JSON-mode service requires explicit JSON prompt')
                if 'Grade the target answer' in system:return json.dumps({'score':3,'reason':'synthetic'})
                return json.dumps(wire if '"checks"' in system else self.o,ensure_ascii=False)
        self.provider=Provider()
        self.answers=[{'q_id':self.a['question_id'],'answer_id':self.a['answer_id'],'answer':self.a['text']}]

    def plan(self):
        return freeze_plan(self.q,self.r,self.answers,model_config=provider_config(self.provider),label_version='synthetic-v1')

    def test_full_comparison_delays_truth_and_accounts_for_each_method(self):
        def truth():
            self.assertEqual(self.provider.calls,3)
            return [{'answer_id':self.a['answer_id'],'score':3}]
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'run'
            summary=run_plan(self.plan(),provider=self.provider,output_dir=dest,truth_loader=truth)
            self.assertEqual(summary['accounting']['usage_totals']['total_tokens'],45)
            for mode in ('direct','structured','checklist'):
                self.assertEqual(summary['reports'][mode]['all_model_suggestions']['mae'],0)
                row=json.loads((dest/(mode+'.jsonl')).read_text())
                self.assertEqual(row['metadata']['usage_summary']['usage_totals']['total_tokens'],15)
            with self.assertRaises(FileExistsError):run_plan(self.plan(),provider=self.provider,output_dir=dest,truth_loader=truth)
            self.assertEqual(self.provider.calls,3)

    def test_mutated_plan_wrong_model_and_missing_calibration_fail_before_spending(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan=self.plan();plan['answers'][0]['answer']='mutated'
            with self.assertRaises(ValidationError):run_plan(plan,provider=self.provider,output_dir=Path(tmp)/'a',truth_loader=lambda:[])
            plan=self.plan();self.provider.model='other'
            with self.assertRaises(ValidationError):run_plan(plan,provider=self.provider,output_dir=Path(tmp)/'b',truth_loader=lambda:[])
        with self.assertRaises(ValidationError):
            freeze_plan(self.q,self.r,self.answers,modes=['calibrated'],cases=[],model_config=provider_config(self.provider),label_version='v1')
        with self.assertRaises(ValidationError):
            freeze_plan(self.q,self.r,[dict(self.answers[0],score=3)],model_config=provider_config(self.provider),label_version='v1')
        self.assertEqual(self.provider.calls,0)
