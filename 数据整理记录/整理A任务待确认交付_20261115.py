"""Prepare A's provenance, development splits and blank teacher forms; no regrading."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / '题目答案相关数据'
OUT = ROOT / 'A任务修正_2026-11-15'
LABEL_VERSION = '2026-11-10-user-approved-v1'
META_VERSION = '2026-11-15-a-provenance-v1'
BROOT = ROOT / 'review-b-2026-11-15/unpacked/2026-ict-1'
B_RUBRIC = BROOT / 'examples/q011_checklist_draft.json'
PRIORITY = ['011-000102', '011-000083', '011-000046', '011-000069',
            '011-000020', '011-000037', '011-000057', '011-000053',
            '011-000085', '010-000076', '010-000109']


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in rows), encoding='utf-8')


def jsonl_once(path, rows):
    if not path.exists():
        jsonl(path, rows)


def dump_once(path, obj):
    if not path.exists():
        dump(path, obj)


def text_once(path, text):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')


def text_key(text):
    return re.sub(r'\s+', '', text).casefold()


def main():
    if (DATA/'08_本轮暂行修订入口_2026-11-16.md').exists():
        raise RuntimeError('当前数据已更新为2026-11-16暂行版；本脚本是11月15日历史版本，不再重写旧标签索引。')
    OUT.mkdir(exist_ok=True)
    protected = [p for p in DATA.rglob('*') if p.is_file()]
    protected += [ROOT/'all.jsonl', ROOT/'排序后的数据.jsonl']
    before = {str(p.relative_to(ROOT)): sha(p) for p in protected}
    reviews = {r['answer_id']: r for r in read_rows(DATA/'03_待教师复核记录.jsonl')}
    rubric011 = json.loads(B_RUBRIC.read_text(encoding='utf-8'))
    assert rubric011['status'] == 'DRAFT'
    metadata, splits, duplicate_groups, question_versions, statistics = [], [], [], [], []
    all_rows, qrefs = {}, {}
    for folder in sorted(p for p in DATA.iterdir() if p.is_dir() and p.name.isdigit()):
        qid = folder.name
        paths = {n: next(folder.glob(n + '_*')) for n in ('01','02','03','04','05')}
        records = {n: read_rows(paths[n]) for n in ('02','03','04','05')}
        rows = records['05']
        for scored, unscored in [('05','04'),('03','02')]:
            assert [{k:v for k,v in r.items() if k!='score'} for r in records[scored]] == records[unscored]
        index = {r['answer_id']:r for r in rows}
        assert len(index) == len(rows)
        sample_ids = {r['answer_id'] for r in records['03']}
        assert all(index[r['answer_id']] == r for r in records['03'])
        groups = defaultdict(list)
        for row in rows:
            assert row['q_id'] == qid and 0 <= float(row['score']) <= 10
            assert row['answer_id'] not in all_rows
            all_rows[row['answer_id']] = row
            groups[text_key(row['answer'])].append(row)
        rule_sha = sha(paths['01'])
        question_versions.append({'question_id':qid,'label_set_version':LABEL_VERSION,
            'local_question_document':str(paths['01'].relative_to(ROOT)),
            'local_question_document_sha256':rule_sha,
            'rubric_review_status':'DRAFT_NOT_FULLY_TEACHER_APPROVED',
            'b_runtime_rubric_version':rubric011['version'] if qid=='011' else None,
            'b_runtime_rubric_sha256':sha(B_RUBRIC) if qid=='011' else None,
            'published_teacher_rubric_version':None,
            'score_scale_used_in_existing_experiments':10,
            'original_exam_maximum_independently_verified':False})
        qrefs[qid] = question_versions[-1]
        exclusions = []
        for key, members in sorted(groups.items()):
            assert len({float(r['score']) for r in members}) == 1, 'same-text conflicting score; manual review needed'
            gid = hashlib.sha256((qid+'\0'+key).encode()).hexdigest()
            learning_group = any(r['answer_id'] in sample_ids for r in members)
            pool = 'learning' if learning_group else 'development'
            subset = None if learning_group else 'development_' + ('a' if int(gid[-1],16)%2 == 0 else 'b')
            if len(members)>1:
                duplicate_groups.append({'question_id':qid,'group_sha256':gid,
                    'answer_ids':[r['answer_id'] for r in members], 'pool':pool,
                    'development_subset':subset,'normalization':'remove Unicode whitespace; casefold'})
            for row in members:
                aid = row['answer_id']
                review = reviews.get(aid,{})
                status = review.get('review_status')
                historical_teacher = review.get('teacher_score')
                if status == 'user_approved_applied': origin = 'user_approved_revision'
                elif historical_teacher is not None and float(historical_teacher)==float(row['score']): origin='recorded_teacher_total_review'
                else: origin='dataset_original_label'
                if historical_teacher is not None and review.get('current_score') is not None:
                    assert float(review['current_score']) == float(row['score'])
                meta = {'question_id':qid,'answer_id':aid,'label_set_version':LABEL_VERSION,
                    'metadata_version':META_VERSION,'current_reference_score':float(row['score']),
                    'current_score_origin':origin,'recorded_teacher_total_score':historical_teacher,
                    'recorded_teacher_total_matches_current':None if historical_teacher is None else float(historical_teacher)==float(row['score']),
                    'teacher_item_review_recorded':False,'approved_for_case_reuse':False,
                    'runtime_case_eligible':False,'review_record_status':status,
                    'review_record_source':'题目答案相关数据/03_待教师复核记录.jsonl' if review else None,
                    'answer_text_sha256':hashlib.sha256(row['answer'].encode()).hexdigest(),
                    'question_document_sha256':rule_sha,
                    'b_runtime_rubric_version':qrefs[qid]['b_runtime_rubric_version'],
                    'published_teacher_rubric_version':None,
                    'score_file':str(paths['05'].relative_to(ROOT))}
                metadata.append(meta)
                role = 'learning_example' if aid in sample_ids else ('learning_duplicate' if learning_group else 'development_candidate')
                if role == 'learning_duplicate': exclusions.append(aid)
                splits.append({'question_id':qid,'answer_id':aid,'label_set_version':LABEL_VERSION,
                    'exact_text_group_sha256':gid,'role':role,'pool':pool,'development_subset':subset,
                    'final_test_eligible':False,'exposure_status':'existing rule-design/development data or exposure not independently certified',
                    'near_duplicate_review_complete':False})
        statistics.append({'question_id':qid,'answers':len(rows),'learning_examples':len(sample_ids),
            'exact_text_groups':len(groups),'additional_learning_duplicate_exclusions':len(exclusions),
            'development_candidates':len(rows)-len(sample_ids)-len(exclusions),
            'blank_answers':sum(not r['answer'].strip() for r in rows),
            'blank_nonzero':sum(not r['answer'].strip() and float(r['score'])!=0 for r in rows)})
    metadata.sort(key=lambda r:r['answer_id']); splits.sort(key=lambda r:r['answer_id'])
    assert len(metadata)==1582 and len({r['answer_id'] for r in metadata})==1582
    assert not any(r['runtime_case_eligible'] for r in metadata)
    assert not any(r['final_test_eligible'] for r in splits)
    grouping = defaultdict(set)
    for row in splits:grouping[row['exact_text_group_sha256']].add((row['pool'],row['development_subset']))
    assert all(len(v)==1 for v in grouping.values())
    forms=[]
    for aid in PRIORITY:
        row=all_rows[aid];qid=row['q_id']
        if qid=='011':criteria=rubric011['criteria']; version=rubric011['version']
        else:
            criteria=[{'criterion_id':'010-P1','max_score':2},{'criterion_id':'010-P2','max_score':3},{'criterion_id':'010-P3','max_score':5}]
            version='A-010-local-proposal-2026-11-15'
        forms.append({'question_id':qid,'answer_id':aid,'answer_text':row['answer'],
            'answer_text_sha256':hashlib.sha256(row['answer'].encode()).hexdigest(),
            'source_label_set_version':LABEL_VERSION,'source_proposed_rubric_version':version,
            'rubric_version':None,'rubric_status':'DRAFT','teacher_confirmed':False,
            'approved_for_reuse':False,'review_id':None,'reviewer_id':None,'confirmed_at':None,
            'teacher_total_score':None,'confirmed_items':None,
            'proposed_item_template':[{'criterion_id':c['criterion_id'],'proposed_max_score':c['max_score'],
                'score':None,'teacher_reason':None,'evidence':[]} for c in criteria],
            'note':'待教师填写；建议配分也待批准。最终完整逐项分请填写confirmed_items并绑定教师发布版本，不能直接作为运行案例。'})
    jsonl(OUT/'数据与版本/逐条标签来源与版本.jsonl',metadata)
    jsonl(OUT/'数据与版本/开发用途划分清单.jsonl',splits)
    dump(OUT/'数据与版本/题目与规则版本映射.json',question_versions)
    dump(OUT/'数据与版本/同文分组.json',duplicate_groups)
    jsonl_once(OUT/'教师填写/逐项评分与复用批准_待填写.jsonl',forms)
    dump_once(OUT/'教师填写/规则审核_待填写.json',{'status':'DRAFT','teacher_confirmed':False,
        'reviewer_id':None,'confirmed_at':None,'published_rubric_version':None,
        'questions':[{'question_id':q,'proposed_max_score':10,'confirmed_max_score':None,
            'criterion_weights_confirmed':False,'allowed_score_levels':None,'equivalence_policy_confirmed':False,
            'empty_answer_policy':None,'typo_policy':None,'missing_branch_policy':None,'teacher_notes':None} for q in ['011','010']]})
    jsonl_once(OUT/'教师填写/证据语义审核_空白模板.jsonl',[{'answer_id':None,'criterion_id':None,
        'assessment_sha256':None,'teacher_confirmed':False,'reviewer_id':None,'verdict':None,'review_note':None}])
    jsonl_once(OUT/'教师填写/人工工时_空白模板.jsonl',[{'event_id':None,'reviewer_id':None,'phase':None,
        'seconds':None,'batch_id':None,'answer_ids':[],'recorded_at':None,'note':None}])
    jsonl_once(OUT/'教师填写/练习审核_空白模板.jsonl',[{'practice_id':None,'question_id':None,'source_question_id':'011',
        'criterion_ids':[],'text':None,'reference_answer':None,'difficulty':None,'teacher_approved':False,
        'reviewer_id':None,'reviewed_at':None,'note':'没有虚构审核练习；请A提供、教师审核后录入。'}])
    text_once(OUT/'教师填写/新采集独立测试答案_空.jsonl','')
    text_once(OUT/'教师填写/真实审核案例_空.jsonl','')
    # Frozen draft keeps the precise rubric B expects; the status is not promoted.
    (OUT/'数据与版本/011_机器规则草案_仅供教师审阅.json').write_bytes(B_RUBRIC.read_bytes())
    # Model-input files intentionally omit all labels, reviews and model outputs.
    by_id={r['answer_id']:r for r in splits}
    for qid in sorted(qrefs):
        qrows=[r for r in metadata if r['question_id']==qid]
        for pool in ('learning','development_a','development_b'):
            chosen=[all_rows[r['answer_id']] for r in qrows if (by_id[r['answer_id']]['role']=='learning_example' if pool=='learning' else by_id[r['answer_id']]['development_subset']==pool)]
            plain=[{k:r[k] for k in ('answer_id','q_id','answer')} for r in chosen]
            jsonl(OUT/f'数据与版本/无分开发输入/{qid}/{pool}.jsonl',plain)
    source_inventory={'metadata_version':META_VERSION,'label_set_version':LABEL_VERSION,
        'source_files_sha256':before,'b_source_commit':'ca38a5b99ef6c77adb73e4d5235520646b61ef33',
        'b_rubric_sha256':sha(B_RUBRIC),'published_teacher_rubric_version':None}
    dump(OUT/'数据与版本/来源文件摘要.json',source_inventory)
    summary={'metadata_version':META_VERSION,'label_set_version':LABEL_VERSION,'questions':len(statistics),
        'answers':len(metadata),'label_origins':dict(Counter(r['current_score_origin'] for r in metadata)),
        'learning_examples':sum(r['learning_examples'] for r in statistics),
        'additional_learning_duplicate_exclusions':sum(r['additional_learning_duplicate_exclusions'] for r in statistics),
        'development_candidates':sum(r['development_candidates'] for r in statistics),
        'final_test_answers':0,'runtime_approved_cases':0,'teacher_forms':len(forms),
        'blank_nonzero':sum(r['blank_nonzero'] for r in statistics),'question_statistics':statistics,
        'source_scores_changed':False,'source_answers_changed':False,'near_duplicate_review_complete':False}
    for rel,value in before.items():assert sha(ROOT/rel)==value, 'Source changed during preparation: '+rel
    dump(OUT/'数据与版本/整理核验结果.json',summary)
    # Teacher-first document keeps current/model/proposed scores outside the blind form.
    intro='''# 教师求证与独立逐项审核表\n\n状态：待教师审核；没有预填教师分数、签字或复用批准。建议先独立填写本表，再查看另一份初步核查说明及历史标签，减少先看模型分数带来的影响。\n\n## 先确认统一规则\n\n1. 第011题是否按10分、入队2/输出栈非空出队2/输出栈空出队3/判空2/FIFO说明1配分？各档部分分怎么给？\n2. 是否接受每次还原到单栈或入队时倒腾的正确替代算法？单持久栈判空是否可得完整分？题目是否要求复杂度？\n3. 省略非空前提、空队列保护、代码变量笔误、文字与代码冲突分别怎样处理？正确概念能否单独得分？\n4. 第010题是否按2/3/5配分？正确完整逆序序列是否等价于通项？\n5. 确认一份成绩后，是否另行批准其作为后续评分案例？这两项请分开决定。\n\n审核者：____；日期：____；规则发布版本：____；规则修订意见：____。\n\n原始考试满分元数据尚未独立核验，本包的10分是现行开发尺度，仍请确认。前两份（102、083）优先，其余可分批完成。\n'''
    parts=[intro]
    for i,form in enumerate(forms,1):
        qid=form['question_id']
        question=(DATA/qid/'01_题目与参考答案.md').read_text(encoding='utf-8').split('## 题目')[1].split('## 满分')[0].strip()
        parts += [f"\n## {i}. {form['answer_id']}\n\n题目：{question}\n\n学生原文：\n\n```text\n{form['answer_text']}\n```\n\n"]
        parts += ['| 分项（配分仍待确认） | 建议上限 | 教师分 | 教师理由及原文依据 |\n| --- | ---: | --- | --- |\n']
        for c in form['proposed_item_template']:
            parts += [f"| {c['criterion_id']} | {c['proposed_max_score']} | ____ | ____ |\n"]
        parts += ['\n总分：____；是否需要修订通用规则：____；仅确认本份成绩：[ ]；另行批准案例复用：[ ]；不批准复用：[ ]。\n\n如批准复用，请补审核编号、审核者、时间和最终规则版本；未经明确填写不得自动推定批准。\n']
    text_once(OUT/'02_给老师的求证与逐项审核表.md',''.join(parts))
    audit=['# 当前数据与版本整理结果\n\n本次补齐元数据和开发用途，不修改既有分数、学生答案、历史审核记录或模型输出。\n\n',
        f"共{len(statistics)}题、{len(metadata)}条；学习样本165条；另发现{summary['additional_learning_duplicate_exclusions']}条与学习样本同文但编号不同的记录，已从开发候选排除；剩余开发候选{summary['development_candidates']}条。最终测试0条，真实逐项审核案例0条。\n\n",
        '| 题号 | 全量 | 学习样本 | 额外同文排除 | 开发候选 |\n| --- | ---: | ---: | ---: | ---: |\n']
    for s in statistics:audit.append(f"| {s['question_id']} | {s['answers']} | {s['learning_examples']} | {s['additional_learning_duplicate_exclusions']} | {s['development_candidates']} |\n")
    audit += ['\n开发a/b按同题去空白、忽略大小写的同文组稳定分配，不跨组拆分；这是开发子集，不是封存最终测试。未完成语义近重复及同学生归组（现有数据缺少学生身份字段）。\n\n',
        '逐条标签来源：'+json.dumps(summary['label_origins'],ensure_ascii=False)+'。历史教师总分与当前用户修订分分开记录，没有把总分记录冒充逐项教师确认。\n\n',
        '来源摘要只记录本地实际文件；原始数据外发/公开范围沿用原来源说明，本次未新增授权或外发数据。规则文档摘要、标签集版本和B运行规则版本分别存储，不能用同一个版本号替代。\n']
    (OUT/'04_数据核查与划分结果.md').write_text(''.join(audit),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='question_statistics'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
