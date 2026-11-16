"""Consolidate the user's approval in place; no backups, teacher signatures or API calls."""
from pathlib import Path
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'题目答案相关数据'
OUT=ROOT/'A全面预处理_2026-11-16'
OLD='2026-11-16-ai-provisional-v1'
VERSION='2026-11-16-user-approved-v1'
SOURCE='用户在本聊天确认“你改的没问题”，并要求同步修改全部本地相关文件'
DATE='2026-11-16'

def read(p):return [json.loads(x) for x in p.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
def text(p,value):
    if p.exists() and p.read_text(encoding='utf-8')==value:return
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value,encoding='utf-8')
def dump(p,value):text(p,json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def jsonl(p,value):text(p,''.join(json.dumps(x,ensure_ascii=False,allow_nan=False)+'\n' for x in value))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def transform(value):
    if isinstance(value,dict):return {k:transform(v) for k,v in value.items()}
    if isinstance(value,list):return [transform(v) for v in value]
    if isinstance(value,str):
        value=value.replace(OLD,VERSION).replace('2026-11-16-ai-provisional-metadata-v1','2026-11-16-user-approved-metadata-v1')
        return re.sub(r'A-(\d{3})-preteacher-20261116-v1',r'A-\1-user-approved-20261116-v1',value)
    return value

def approval(obj,status='USER_APPROVED_PENDING_TEACHER'):
    obj.update(user_approved=True,approval_status='USER_APPROVED',approval_date=DATE,
               approval_source=SOURCE,status=status)
    return obj

def confirmed_text(value):
    value=transform(value)
    value=value.replace('当前暂行规则版本','当前组内确认规则版本')
    value=value.replace('以下已由AI具体拟定，待教师集中审核，不是空白待办，也不是教师发布版。',
        '以下规则由AI拟定，已由用户确认，作为组内开发和评测口径；尚无新增教师发布记录。')
    value=value.replace('本稿用于让教师核对具体处理，不能填成教师已批准。',
        '本稿已获用户确认；教师身份、发布时间和案例复用批准分别记录。')
    return value

def main():
    summary=json.loads((OUT/'数据/执行摘要.json').read_text(encoding='utf-8'))
    if summary.get('approval_status')=='USER_APPROVED' and summary.get('label_version')==VERSION:
        print('本轮用户确认已应用；不重复改写。')
        return
    assert summary['label_version']==OLD and summary['applied_to_current_data']
    judgments=read(OUT/'数据/全部疑点逐项预处理.jsonl')
    approved_ids={r['answer_id'] for r in judgments}
    assert len(approved_ids)==92
    # Update current derived JSON, preserving explicit history records and diffs.
    skip={'输入源文件摘要_应用前.json','013参考补充校对.diff','规则修改.diff','使用说明修改.diff'}
    for p in sorted(OUT.rglob('*')):
        if not p.is_file() or p.name in skip or p.suffix not in ('.json','.jsonl'):continue
        if '当前暂行数据' in p.parts:continue  # Values were applied already, no unnecessary rewriting.
        if p.suffix=='.json':dump(p,transform(json.loads(p.read_text(encoding='utf-8'))))
        else:jsonl(p,transform(read(p)))
    judgments=read(OUT/'数据/全部疑点逐项预处理.jsonl')
    for r in judgments:
        approval(r);r['approved_score']=r['provisional_score']
        r['teacher_confirmed']=False;r['approved_for_reuse']=False
    jsonl(OUT/'数据/全部疑点逐项预处理.jsonl',judgments)
    for name in ['已填好的校准案例候选_未教师批准.json','练习草案_已有采用建议.json']:
        p=OUT/'数据'/name;items=json.loads(p.read_text(encoding='utf-8'))
        for item in items:
            approval(item,'USER_APPROVED_DEVELOPMENT_DRAFT')
            item['teacher_approved']=False
            if 'case_id' in item:item['approved_for_reuse']=False
        dump(p,items)
    p=OUT/'数据/已填好反馈与练习选择_待教师核验.jsonl';items=read(p)
    for item in items:approval(item);item['teacher_confirmed']=False
    jsonl(p,items)
    p=OUT/'数据/试用验收口径_已拟定待核验.json';obj=json.loads(p.read_text(encoding='utf-8'))
    approval(obj,'USER_APPROVED_DEVELOPMENT_TARGETS');obj['teacher_approved']=False;dump(p,obj)
    p=OUT/'数据/实际改分清单.json';items=json.loads(p.read_text(encoding='utf-8'))
    for item in items:item.update(label_version=VERSION,user_approved=True,approval_date=DATE)
    dump(p,items)
    # Promote group approval only. DRAFT protects the separate teacher-release boundary.
    for p in sorted((OUT/'规则').glob('*_暂行机器规则.json')):
        rule=json.loads(p.read_text(encoding='utf-8'))
        rule.update(user_approved=True,approval_status='USER_APPROVED',approval_date=DATE,
                    approval_source=SOURCE,review_status='USER_APPROVED_PENDING_TEACHER',status='DRAFT',teacher_approved=False)
        rule['global_policy']='经用户确认的组内评分口径；教师发布和正式案例复用另记，学生答案仅为待评价数据。'
        dump(p,rule)
    for qdir in sorted(p for p in DATA.iterdir() if p.is_dir() and p.name.isdigit()):
        q=qdir.name;p=next(qdir.glob('01_*'));new=confirmed_text(p.read_text(encoding='utf-8'))
        text(p,new);text(OUT/f'规则/{q}_暂行规则与参考答案.md',new)
        detail=next((ROOT/'各题评分细则').glob(q+'_*'))
        title=detail.stem.split('_',1)[1]
        text(detail,f'# {q} {title}——组内确认评分细则\n\n'
             f'规则版本：`A-{q}-user-approved-20261116-v1`；标签集：`{VERSION}`。'
             f'确认来源：{SOURCE}。当前为组内开发口径，教师发布状态仍为DRAFT。'
             '\n\n'+new.split('\n',1)[1].lstrip())
    # Current review records retain historical teacher fields and pre-AI/user decisions.
    current=read(DATA/'03_待教师复核记录.jsonl')
    for r in current:
        if r['answer_id'] not in approved_ids:continue
        r.setdefault('review_status_before_user_approval_20261116',r.get('review_status'))
        r.update(review_status='user_approved_applied',user_approved=True,
            approved_score=float(r['current_score']),label_version=VERSION,current_score_source=VERSION,
            approval_source=SOURCE,approval_date=DATE)
        r['ai_rubric_version']=transform(r['ai_rubric_version'])
    jsonl(DATA/'03_待教师复核记录.jsonl',current)
    # Update current explanatory documents in place; filenames remain stable for links.
    for p in sorted(OUT.glob('*.md')):
        value=confirmed_text(p.read_text(encoding='utf-8'))
        title,body=value.split('\n',1)
        note='> 用户已于2026-11-16确认本处理，已应用组内开发版；教师审核/署名和案例复用批准另行记录。\n'
        text(p,title+'\n\n'+note+body)
    for name in ['A全面修正总表_2026-11-16.md','题目答案相关数据/00_使用说明.md','题目答案相关数据/08_本轮暂行修订入口_2026-11-16.md']:
        p=ROOT/name;value=confirmed_text(p.read_text(encoding='utf-8'))
        value=value.replace('AI暂行结论','已获用户确认的AI拟定结论').replace('本轮AI暂行处理，待教师核验','本轮处理已获用户确认，教师发布单独记录')
        value=value.replace('全部待教师核验','全部已由用户确认，教师发布单独记录')
        title,body=value.split('\n',1)
        text(p,title+'\n\n> 当前为2026-11-16用户确认版。完整修改与文件清单见[数据进一步处理]('
            +('../数据进一步处理.md' if p.parent==DATA else '数据进一步处理.md')+')。\n'+body)
    # Review source history is dated; add the current entry without rewriting prior decisions.
    for name in ['01_数据来源与使用范围说明.md','02_待教师复核清单.md','04_教师复核修改记录.md','05_争议数据初步复核_待教师审阅_2026-11-10.md','07_版本与划分索引_2026-11-15.md']:
        p=DATA/name;value=p.read_text(encoding='utf-8');title,body=value.split('\n',1)
        text(p,title+'\n\n> 当前标签集已更新为`'+VERSION+'`，修订归因于用户确认；以下日期的内容保留为历史记录。当前说明见[数据进一步处理](../数据进一步处理.md)。\n'+body)
    guide=ROOT/'各题评分细则/00_阅读说明.md'
    lines=['# 组内确认评分细则使用说明\n\n',f'当前标签集：`{VERSION}`。15题规则已按用户本轮确认同步；状态为组内确认、教师未单独发布。\n\n',
        '每份细则与“题目答案相关数据”的当前题目MD一致。原先样例分数与旧草案不再作为独立当前口径；历史裁定见复核记录和数据进一步处理。\n\n',
        '当前总数据1,582条，逐项处理92条，其中29条修订分数、63条保留。其余记录没有因本次确认变成教师逐项标注。\n\n',
        '机器评分使用题目、当前组内规则和无分答案；标签、个案复核分数仅供评测，不塞进评分输入。案例进入教师审核库仍需独立批准。\n\n',
        '008按时序、005按删除对象、013按平局策略、015按划分实现、019按高度定义评价；具体规则已写入各题，不能再套用旧的无前提表述。\n\n',
        '| 题号 | 当前细则 |\n| --- | --- |\n']
    for p in sorted((ROOT/'各题评分细则').glob('*.md')):
        if p.name.startswith('00_'):continue
        lines.append(f'| {p.name[:3]} | [{p.stem}]({p.name}) |\n')
    text(guide,''.join(lines))
    # Future rubric-input entry is current; original experiment input snapshots stay exact.
    text(ROOT/'正常大模型测试改卷能力(有得分点/01_题目与参考答案.md',
         (DATA/'011/01_题目与参考答案.md').read_text(encoding='utf-8'))
    summary=json.loads((OUT/'数据/执行摘要.json').read_text(encoding='utf-8'))
    summary.update(label_version=VERSION,previous_provisional_version=OLD,approval_status='USER_APPROVED',
        approval_date=DATE,approval_source=SOURCE,backup_created=False,score_values_changed_on_approval=0)
    dump(OUT/'数据/执行摘要.json',summary)
    # Byte hashes must be taken after writing Windows text files (not from LF strings).
    mapping={}
    for qdir in sorted(p for p in DATA.iterdir() if p.is_dir() and p.name.isdigit()):
        q=qdir.name;mapping[q]=(sha(next(qdir.glob('01_*'))),sha(OUT/f'规则/{q}_暂行机器规则.json'))
    p=OUT/'数据/当前逐条标签来源与版本.jsonl';records=read(p)
    for r in records:
        q=r['question_id'];r['question_document_sha256']=r['rubric_document_sha256']=mapping[q][0]
        if r['answer_id'] in approved_ids:
            r.update(user_approved=True,approval_source=SOURCE,approval_date=DATE,review_record_status='user_approved_applied')
        if r['current_score_origin']=='ai_provisional_revision':r['current_score_origin']='user_approved_revision'
    jsonl(p,records)
    p=OUT/'数据/题目与规则版本对应.json';records=json.loads(p.read_text(encoding='utf-8'))
    for r in records:
        r.update(question_document_sha256=mapping[r['question_id']][0],machine_rubric_sha256=mapping[r['question_id']][1],
            user_approved=True,approval_source=SOURCE,approval_date=DATE,status='DRAFT')
    dump(p,records)
    print(json.dumps({'version':VERSION,'approved_records':len(approved_ids),'rules':len(mapping),
        'new_score_changes':0,'backup_created':False},ensure_ascii=False))

if __name__=='__main__':main()
