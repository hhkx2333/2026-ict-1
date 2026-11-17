"""Frozen, append-only same-provider development comparisons with explicit live opt-in."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from .core import _require, validate_input
from .checklist import validate_checklist
from .calibration import select_cases
from .datasets import read_jsonl, write_json, index_rows
from .baselines import grade_baseline
from .workflow import grade_submission
from .telemetry import MeteredProvider
from .quality import evaluate_quality, evidence_tasks, compare_predictions

MODES = {'direct','structured','checklist','calibrated'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def code_hashes():
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))}


def provider_config(provider):
    return {key:getattr(provider,key,None) for key in ('endpoint','model','max_tokens','thinking')}


def freeze_plan(question, rubric, answers, *, modes=('direct','structured','checklist'), cases=(),
                model_config, label_version, allow_draft=False, reference_sha256=None):
    _require(isinstance(answers,list) and 1 <= len(answers) <= 200, '1..200 answers required')
    index_rows(answers)
    _require(isinstance(modes,(list,tuple)) and modes and all(isinstance(m,str) and m in MODES for m in modes)
             and len(set(modes))==len(modes), 'invalid or duplicate comparison modes')
    _require(isinstance(cases,(list,tuple)), 'cases must be a list')
    _require(isinstance(model_config,dict) and set(model_config)=={'endpoint','model','max_tokens','thinking'},
             'public model config only; never store API keys in plan')
    _require(isinstance(label_version,str) and label_version.strip(), 'label version required')
    _require(type(allow_draft) is bool, 'allow_draft must be boolean')
    vr = dict(rubric,status='PUBLISHED') if rubric.get('status')=='DRAFT' and allow_draft else rubric
    for row in answers:
        _require(isinstance(row,dict) and set(row)=={'q_id','answer_id','answer'}, 'unscored ADS answers only')
        validate_input(question,vr,{'question_id':row['q_id'],'answer_id':row['answer_id'],'text':row['answer']})
    if 'checklist' in modes:
        validate_checklist(rubric)
    if 'calibrated' in modes:
        _require(cases and rubric['status']=='PUBLISHED', 'calibrated comparison needs published rules and actual approved cases')
        for row in answers:
            select_cases(question,vr,{'question_id':row['q_id'],'answer_id':row['answer_id'],'text':row['answer']},list(cases))
    else:
        _require(not cases, 'cases supplied without calibrated mode')
    _require(reference_sha256 is None or (isinstance(reference_sha256,str) and len(reference_sha256)==64
             and all(c in '0123456789abcdef' for c in reference_sha256)), 'invalid reference fingerprint')
    plan = {'schema_version':'1','purpose':'development; not sealed final evaluation',
            'question':question,'rubric':rubric,'answers':answers,'modes':list(modes),'cases':list(cases),
            'model_config':model_config,'label_version':label_version,'reference_sha256':reference_sha256,'allow_draft':allow_draft,
            'max_calls':len(answers)*sum(1 if m=='direct' else 2 for m in modes),
            'code_sha256':code_hashes()}
    plan = copy.deepcopy(plan)
    plan['plan_sha256'] = digest(plan)
    return plan


def run_plan(plan, *, provider, output_dir, truth_loader):
    _require(isinstance(plan,dict) and plan.get('plan_sha256')==digest({k:v for k,v in plan.items() if k!='plan_sha256'}),
             'frozen plan was modified')
    _require(plan['code_sha256']==code_hashes(), 'code changed since freeze; create a new plan')
    _require(plan['model_config']==provider_config(provider), 'provider configuration differs from frozen plan')
    expected = freeze_plan(plan['question'],plan['rubric'],plan['answers'],modes=plan['modes'],cases=plan['cases'],
                           model_config=plan['model_config'],label_version=plan['label_version'],allow_draft=plan['allow_draft'],reference_sha256=plan['reference_sha256'])
    _require(expected==plan, 'invalid or unsupported frozen plan')
    dest = Path(output_dir)
    dest.mkdir(parents=True,exist_ok=False)
    write_json(dest/'plan.json',plan)
    tracked = MeteredProvider(provider,max_calls=plan['max_calls'])
    outputs = {}
    for mode in plan['modes']:
        results=[]
        with (dest/(mode+'.jsonl')).open('x',encoding='utf-8') as handle:
            for row in plan['answers']:
                answer={'question_id':row['q_id'],'answer_id':row['answer_id'],'text':row['answer']}
                if mode=='direct':
                    per_job=MeteredProvider(tracked,max_calls=1)
                    result=grade_baseline(plan['question'],answer,provider=per_job,max_attempts=1,
                        rubric=plan['rubric'],allow_draft=plan['allow_draft'])
                    result['metadata']['usage_summary']=per_job.summary()
                else:
                    request={'question':plan['question'],'rubric':plan['rubric'],'answer':answer,
                             'mode':'checklist' if mode=='checklist' else 'structured',
                             'cases':plan['cases'] if mode=='calibrated' else [],
                             'allow_draft':plan['allow_draft'],'max_attempts':2}
                    result=grade_submission(request,provider=tracked)
                result['metadata'].update(experiment_mode=mode,plan_sha256=plan['plan_sha256'])
                results.append(result)
                handle.write(json.dumps(result,ensure_ascii=False,allow_nan=False)+'\n');handle.flush()
                write_json(dest/'call_accounting.json',{'calls':tracked.calls,'summary':tracked.summary()})
                print(mode,row['answer_id'],result['status'],result['suggested_total'],flush=True)
        outputs[mode]=results
    # Read and validate references only after ALL model requests. Never pass them to a provider.
    truth=truth_loader()
    _require(set(index_rows(truth))=={r['answer_id'] for r in plan['answers']}, 'truth must match all planned IDs exactly')
    _require(plan['reference_sha256'] is None or digest(truth)==plan['reference_sha256'], 'reference version/hash changed')
    write_json(dest/'truth.json',{'label_version':plan['label_version'],'rows':truth})
    reports={mode:evaluate_quality(rows,truth,maximum=plan['question']['max_score']) for mode,rows in outputs.items()}
    for mode,rows in outputs.items():
        write_json(dest/(mode+'-evidence-audit-tasks.json'),evidence_tasks(rows))
    paired={}
    first=plan['modes'][0]
    for mode in plan['modes'][1:]:
        paired[first+'__'+mode]=compare_predictions(outputs[first],outputs[mode],truth,maximum=plan['question']['max_score'])
    summary={'plan_sha256':plan['plan_sha256'],'label_version':plan['label_version'],
             'reports':reports,'paired_comparisons':paired,'accounting':tracked.summary(),
             'limitations':['development answers previously inspected','no human semantic audits unless separately supplied',
                            'no teacher-calibration benefit established','no precise fee without billing data']}
    write_json(dest/'summary.json',summary)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    freeze=sub.add_parser('freeze')
    for name in ('question','rubric','answers','model-config','label-version','output'):
        freeze.add_argument('--'+name,required=True)
    freeze.add_argument('--modes',nargs='+',choices=sorted(MODES),default=['direct','structured','checklist'])
    freeze.add_argument('--cases')
    freeze.add_argument('--truth', help='Freeze reference hash only; labels are not included in the scoring plan')
    freeze.add_argument('--allow-draft',action='store_true')
    run=sub.add_parser('run');run.add_argument('--plan',required=True);run.add_argument('--truth',required=True)
    run.add_argument('--output',required=True);run.add_argument('--live',action='store_true')
    a=p.parse_args()
    if a.command=='freeze':
        load=lambda path:json.loads(Path(path).read_text(encoding='utf-8'))
        plan=freeze_plan(load(a.question),load(a.rubric),read_jsonl(a.answers),modes=a.modes,
                         cases=load(a.cases) if a.cases else [],model_config=load(a.model_config),
                         label_version=a.label_version,allow_draft=a.allow_draft,
                         reference_sha256=digest(read_jsonl(a.truth)) if a.truth else None)
        with Path(a.output).open('x',encoding='utf-8') as h:json.dump(plan,h,ensure_ascii=False,indent=2)
    else:
        if not a.live:p.error('run requires --live to send these answers to the configured service')
        from .providers import ChatCompletionsProvider
        plan=json.loads(Path(a.plan).read_text(encoding='utf-8'))
        run_plan(plan,provider=ChatCompletionsProvider.from_env(),output_dir=a.output,
                 truth_loader=lambda:read_jsonl(a.truth))

if __name__=='__main__':main()
