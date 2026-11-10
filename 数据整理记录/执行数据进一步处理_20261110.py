from pathlib import Path
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'题目答案相关数据'
VERSION='2026-11-10-user-approved-v1'
APPROVED={'011-000046':4,'011-000069':6,'011-000020':10,'011-000037':10,'011-000057':10,'010-000076':10,'010-000109':10}
REASONS={
'011-000046':'入队方向1分、所描述分支倒栈后弹出1分、判空2分；其余条件与完整搬移、FIFO依据不足。',
'011-000069':'入队方向1分、S2非空条件1分、对应pop1分、判空2分、开头FIFO概念解释1分；入队中的倒栈不借给出队。',
'011-000020':'全部数据每次出队后还原S1；移开上方元素、弹出栈底、再还原，符合FIFO；只判S1为空成立。',
'011-000037':'S1临时、S2持久保存队列；入队反转并还原使队首在S2顶，出队与单栈判空正确。',
'011-000057':'旧元素临时移出，新元素置于S2底部，再还原旧元素；S2顶始终为队首，符合FIFO。',
'010-000076':'原文与010-000109完全相同，包含正确原因与完整逆序序列；按等价序列口径统一10分。',
'010-000109':'原文与010-000076相同，原为10分，本次确认继续10分。'}


def load(p):return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
def save(p,rows):
    data=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows)
    if p.read_text(encoding='utf-8')!=data:p.write_text(data,encoding='utf-8')


def prepend_note(p,note):
    text=p.read_text(encoding='utf-8')
    if note not in text:
        title,rest=text.split('\n',1);p.write_text(title+'\n\n'+note+'\n'+rest.lstrip('\n'),encoding='utf-8')


def main():
    # Update only the active data package; no backup is made.
    original={};current={}
    for q in ['010','011']:
        p=DATA/q/'05_全部答案_有得分.jsonl';rows=load(p)
        for r in rows:
            aid=r['answer_id'];original[aid]=dict(r)
            if aid in APPROVED:r['score']=str(APPROVED[aid])
            current[aid]=r
        save(p,rows)
        sp=DATA/q/'03_学习样本10%_有得分.jsonl';sample=load(sp)
        for i,r in enumerate(sample):sample[i]=dict(current[r['answer_id']])
        save(sp,sample)
        assert {r['score'] for r in rows}=={r['score'] for r in sample}
        assert [{k:v for k,v in r.items() if k!='score'} for r in rows]==load(DATA/q/'04_全部答案_无得分.jsonl')
        assert [{k:v for k,v in r.items() if k!='score'} for r in sample]==load(DATA/q/'02_学习样本10%_无得分.jsonl')
    supplement=DATA/'06_争议数据初步复核_待教师确认_2026-11-10.jsonl'
    entries=load(supplement)
    for r in entries:
        aid=r['answer_id'];r.setdefault('score_before_20261110',r['current_score'])
        r.update(current_score=str(APPROVED[aid]),approved_score=APPROVED[aid],review_status='user_approved_applied',
                 approval_date='2026-11-10',approval_source='用户在本聊天确认初步判断并要求修改本地数据',
                 label_version=VERSION,decision_reason=REASONS[aid],
                 proposal_status='已由用户确认并应用；未冒充教师新增签字',allowed_use_before_confirmation='开发与评测使用当前确认版；本批已参与规则调整，不属于封存测试')
    save(supplement,entries)
    main_path=DATA/'03_待教师复核记录.jsonl';reviews=load(main_path);index={r['answer_id']:r for r in reviews}
    for aid,score in APPROVED.items():
        if aid not in index:
            r=dict(answer_id=aid,q_id=aid[:3],answer=current[aid]['answer'],original_score=original[aid]['score'],
                flags=['additional_semantic_review'],case_ids=['20261110-supplement'],preliminary_assessments=[],teacher_score=None,teacher_reason=None)
            reviews.append(r);index[aid]=r
        r=index[aid]
        # Existing teacher_score remains a historical teacher decision; current
        # revision is attributed to the user, not silently to the teacher.
        r.update(current_score=str(score),approved_score=score,review_status='user_approved_applied',
                 current_score_source=VERSION,label_version=VERSION,approval_date='2026-11-10',approval_source='用户确认',
                 approval_reason=REASONS[aid])
        if r.get('teacher_score') is not None:
            r['teacher_score_applies_to_current_score']=float(r['teacher_score'])==score
    save(main_path,reviews)
    # Current rules: reflect accepted decisions while retaining proposal status
    # for the parts that have not received a complete teacher rubric approval.
    p=DATA/'010/01_题目与参考答案.md';s=p.read_text(encoding='utf-8')
    s=s.replace('老师对D03两条同文答案分别保留9、10分，该差别尚不能推导成唯一通用扣分规则；涉及这两条时沿用确认值。',
                '2026年11月10日用户确认将D03两条同文答案统一为10分；正确的完整逆序序列与正确通项按等价结果处理。')
    p.write_text(s,encoding='utf-8')
    p=DATA/'011/01_题目与参考答案.md';s=p.read_text(encoding='utf-8')
    s=s.replace('新元素压入输入栈S1；互换栈名但全程一致也接受。','新元素压入输入栈S1；互换栈名但全程一致也接受。方向可辨但变量笔误或关键操作不完整，可给1分，不视为完整正确。')
    s=s.replace('S2空才倒栈1分；把S1全部逐个倒入S2给1分；再弹出S2栈顶1分。','S2空才倒栈1分；在相应出队分支把S1全部逐个倒入S2给1分；再弹出S2栈顶1分。入队函数的条件性倒栈不能借作出队证据。')
    s=s.replace('说明倒栈后先入元素成为队首，或给出有效操作示例；清楚的过程可隐含这一点。','正确说明倒栈与FIFO关系，或给出有效操作示例；清楚的过程可隐含这一点。正确概念解释可单计此1分，不据此补出代码缺失流程。')
    note='- 数据与规则补充版本：2026-11-10-user-approved-v1。用户已确认046为4分、069为6分；020、037、057的正确单栈还原/临时辅助方案均为10分。适用前提是题干未要求复杂度，按合法操作理解每次出队移除一个队首。该确认不等于整套通用细则已由教师签字。\n'
    if note not in s:s=s.rstrip()+'\n'+note
    p.write_text(s,encoding='utf-8')
    # Update the active scored-rubric input; preserve previously captured
    # experiment-input snapshots as records of what the model actually saw.
    p2=ROOT/'正常大模型测试改卷能力(有得分点/01_题目与参考答案.md'
    p2.write_text(p.read_text(encoding='utf-8'),encoding='utf-8')
    for name in ['010_队列接栈的输出序列.md','011_双栈模拟队列.md']:
        p=ROOT/'各题评分细则'/name
        prepend_note(p,'> 2026年11月10日更新：本文件保留早期草案与历史样例分数；当前已确认分数及011补充口径以[数据进一步处理](../数据进一步处理.md)和“题目答案相关数据”目录为准。')
    for name in ['02_待教师复核清单.md','04_教师复核修改记录.md']:
        prepend_note(DATA/name,'> 2026年11月10日补充：用户已确认7条后续修订，其中6条实际改分；010-000076与010-000109现统一10分。下文旧分及此前教师填写保留为历史依据，当前值见[数据进一步处理](../数据进一步处理.md)。')
    prepend_note(DATA/'05_争议数据初步复核_待教师审阅_2026-11-10.md',
        '> 执行状态：2026年11月10日用户已确认本稿建议并要求应用，7条记录已处理、6条实际改分。以下为确认前的审阅依据，教师填写栏继续保留；本次执行归因于用户确认，未伪造教师签字。详见[数据进一步处理](../数据进一步处理.md)。')
    p=DATA/'00_使用说明.md';s=p.read_text(encoding='utf-8')
    s=s.replace('11月10日新增：[争议数据初步复核稿（待教师审阅）](05_争议数据初步复核_待教师审阅_2026-11-10.md)，含046、069及相关待审案例；建议分尚未写入正式答案数据。',
        '当前数据版本：2026-11-10-user-approved-v1。用户已确认7条后续修订，6条实际改分；执行说明与完整文件清单见[数据进一步处理](../数据进一步处理.md)。[审阅稿](05_争议数据初步复核_待教师审阅_2026-11-10.md)与[逐条确认记录](06_争议数据初步复核_待教师确认_2026-11-10.jsonl)已同步执行状态。')
    s=s.replace('score以教师确认后的当前分数为准；未复核记录仍沿用数据集原标签。','score为当前人工基准，包含已有教师修订及本次用户确认修订；来源在复核记录中区分，未复核记录仍沿用数据集原标签。')
    s=s.replace('已应用教师确认修改。','已应用教师及用户确认修改，具体来源见复核记录。')
    p.write_text(s,encoding='utf-8')
    p=DATA/'01_数据来源与使用范围说明.md'
    prepend_note(p,'> 数据版本补充（2026年11月10日）：本次按用户确认更新7条记录，其中6条改分、学生答案未改；不将该确认冒称为新的教师签字。来源下载文件与其校验值保留不变，当前整理版的进一步改动见[数据进一步处理](../数据进一步处理.md)。')
    for aid,value in APPROVED.items():assert current[aid]['score']==str(value)
    print(json.dumps(dict(version=VERSION,approved_records=7,actual_score_changes=sum(original[i]['score']!=str(v) for i,v in APPROVED.items()),sample_membership_changed=False,backup_created=False),ensure_ascii=False))


if __name__=='__main__':main()
