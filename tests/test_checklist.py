import copy
import json
import unittest
from backend.grading.checklist import canonicalize, locate_quotes, grade_checked_answer, validate_checklist, source_units, resolve_evidence
from backend.grading.core import ValidationError
from examples.generate_fixture import build_fixture
from tests.test_grading import SequenceProvider


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.f=build_fixture()
        self.q,self.r,self.a=[self.f[k] for k in ('question','rubric','answer')]
        self.wire={'items':[],'review_reasons':[]}
        for c,row in zip(self.r['criteria'],self.f['model_output']['items']):
            c['checks']=[{'check_id':c['criterion_id']+'-a','description':c['description'],
                         'max_score':c['max_score'],'allowed_scores':c['allowed_scores']}]
            self.wire['items'].append({'criterion_id':c['criterion_id'],'checks':[
                {'check_id':c['criterion_id']+'-a','score':row['suggested_score'],
                 'evidence':[{'quote':e['quote']} for e in row['evidence']],
                 'reason':row['reason'],'flags':[]}]})

    def test_quote_only_gives_compatible_program_calculated_scores(self):
        p=SequenceProvider([self.wire])
        result=grade_checked_answer(self.q,self.r,self.a,provider=p)
        self.assertEqual(result['suggested_total'],3)
        self.assertEqual(len(result['metadata']['check_results']),4)
        self.assertEqual(result['metadata']['prompt_version'],'checklist-operation-context-v3.3')
        self.assertEqual(result['metadata']['evidence_offset_repairs'],[])

    def test_all_repeated_exact_locations_are_explicit_not_guessed(self):
        self.assertEqual(locate_quotes([{'quote':'甲乙'}],'甲乙\n甲乙'),[
            {'quote':'甲乙','start':0,'end':2},{'quote':'甲乙','start':3,'end':5}])
        self.assertEqual(len(locate_quotes([{'quote':'aaa'}],'aaaa')),2)

    def test_changed_whitespace_fake_quote_and_excess_repetition_rejected(self):
        for quote,text in [('甲 乙','甲乙'),('虚构','原文'),('a','a'*9)]:
            with self.subTest(quote=quote),self.assertRaises(ValidationError):
                locate_quotes([{'quote':quote}],text)

    def test_non_numeric_illegal_and_positive_no_evidence_rejected(self):
        for change in ({'score':True},{'score':3},{'evidence':[]},{'flags':['missing_content']}):
            bad=copy.deepcopy(self.wire)
            bad['items'][0]['checks'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValidationError):
                canonicalize(json.dumps(bad),self.r,self.a)

    def test_no_model_totals_missing_and_duplicate_checks(self):
        for mutation in ('total','missing','duplicate'):
            bad=copy.deepcopy(self.wire)
            if mutation=='total':bad['suggested_total']=8
            elif mutation=='missing':bad['items'][0]['checks']=[]
            else:bad['items'][0]['checks']*=2
            with self.subTest(mutation=mutation),self.assertRaises(ValidationError):
                canonicalize(json.dumps(bad),self.r,self.a)

    def test_semantic_risk_and_targeted_prior_assessment(self):
        self.wire['items'][0]['checks'][0]['flags']=['RULE_AMBIGUITY']
        p=SequenceProvider([self.wire,self.wire])
        result=grade_checked_answer(self.q,self.r,self.a,provider=p)
        self.assertEqual(result['status'],'REVIEW_REQUIRED')
        self.assertEqual(result['metadata']['risk_codes'],['RULE_AMBIGUITY'])
        self.assertIn('first_assessment',p.calls[1]['targeted_review'])

    def test_adapter_validation_failure_repaired_as_output_not_transport(self):
        bad=copy.deepcopy(self.wire)
        bad['items'][0]['checks'][0]['evidence']=[{'quote':'虚构'}]
        p=SequenceProvider([bad,self.wire])
        result=grade_checked_answer(self.q,self.r,self.a,provider=p)
        self.assertEqual(result['suggested_total'],3)
        self.assertIn('quote does not occur',p.calls[1]['validation_feedback'])

    def test_bad_check_maxima_fail_before_any_model_call(self):
        self.r['criteria'][0]['checks'][0]['max_score']=1
        p=SequenceProvider([])
        with self.assertRaises(ValidationError):
            grade_checked_answer(self.q,self.r,self.a,provider=p)
        self.assertEqual(p.calls,[])

    def test_source_refs_preserve_unicode_crlf_indent_and_duplicate_identity(self):
        text = '😀甲\r\n\r\n    pop(S2,x);\n😀甲'
        units = source_units(text)
        self.assertEqual([u['source_id'] for u in units], ['L1', 'L3', 'L4'])
        for u in units:
            self.assertEqual(text[u['start']:u['end']], u['text'])
        spans = resolve_evidence([{'source_id': 'L4'}], text)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]['start'], text.rfind('😀甲'))
        for invalid in ('L2', 'L999', [], None):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                resolve_evidence([{'source_id': invalid}], text)

    def test_source_units_reach_provider_without_mutating_caller(self):
        before = json.dumps(self.a, sort_keys=True)
        units = source_units(self.a['text'])
        wire = copy.deepcopy(self.wire)
        for row in wire['items']:
            for check in row['checks']:
                if check['score'] > 0:
                    check['evidence'] = [{'source_id': units[0]['source_id']}]
        p = SequenceProvider([wire])
        result = grade_checked_answer(self.q, self.r, self.a, provider=p)
        self.assertEqual(result['suggested_total'], 3)
        self.assertEqual(p.calls[0]['source_units'][0],
                         {'source_id': units[0]['source_id'], 'text': units[0]['text'], 'operation': 'unspecified'})
        self.assertEqual(before, json.dumps(self.a, sort_keys=True))

    def test_malformed_checklist_and_unrepresentable_subtotals_fail_before_call(self):
        variants = [None, {}, {'criteria': [None]}]
        r = copy.deepcopy(self.r)
        r['criteria'][0]['rules']['second-policy'] = 'ambiguous aggregate mapping'
        variants.append(r)
        r = copy.deepcopy(self.r)
        r['criteria'][0]['checks'][0]['allowed_scores'] = [0, 0.5, 2]
        variants.append(r)
        for rubric in variants:
            p = SequenceProvider([])
            with self.subTest(rubric=rubric), self.assertRaises(ValidationError):
                grade_checked_answer(self.q, rubric, self.a, provider=p)
            self.assertEqual(p.calls, [])

    def test_checklist_cli_uses_source_protocol_and_keeps_existing_results(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from backend.grading.cli import main
        from backend.grading.datasets import read_jsonl
        units = source_units(self.a['text'])
        wire = copy.deepcopy(self.wire)
        for row in wire['items']:
            for check in row['checks']:
                if check['score'] > 0:
                    check['evidence'] = [{'source_id': units[0]['source_id']}]
        provider = SequenceProvider([wire])
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base/'q.json').write_text(json.dumps(self.q))
            (base/'r.json').write_text(json.dumps(self.r))
            (base/'a.jsonl').write_text(json.dumps({'answer_id': self.a['answer_id'],
                'q_id': self.q['question_id'], 'answer': self.a['text']}))
            argv = ['cli', 'run', '--mode', 'checklist', '--live', '--question', str(base/'q.json'),
                '--rubric', str(base/'r.json'), '--answers', str(base/'a.jsonl'), '--output', str(base/'out.jsonl')]
            with patch('sys.argv', argv), patch('backend.grading.cli.ChatCompletionsProvider.from_env', return_value=provider):
                main()
                result = read_jsonl(base/'out.jsonl')[0]
                self.assertEqual(result['suggested_total'], 3)
                self.assertEqual(result['metadata']['mode'], 'checklist')
                self.assertIn('source_units', provider.calls[0])
                with self.assertRaises(FileExistsError):
                    main()
            self.assertEqual(len(provider.calls), 1)
