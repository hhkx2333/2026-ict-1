"""Recompute saved model outputs against the active label version; no model call."""
from pathlib import Path
from collections import Counter, defaultdict
import hashlib
import json
import numpy as np
from scipy.stats import pearsonr,spearmanr

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'题目答案相关数据'
VERSION='2026-11-10-user-approved-v1'


def load(p):return [json.loads(s) for s in p.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def metrics(rows):
    y=np.array([r['reference_score'] for r in rows],float);p=np.array([r['model_score'] for r in rows],float);e=p-y
    cm=np.zeros((11,11),int)
    for a,b in zip(y,p):cm[int(a),int(b)]+=1
    w=(np.arange(11)[:,None]-np.arange(11)[None,:])**2
    den=float((w*np.outer(cm.sum(1),cm.sum(0))/len(rows)).sum())
    return dict(n=len(rows),reference_mean=float(y.mean()),model_mean=float(p.mean()),mae=float(abs(e).mean()),
        rmse=float(np.sqrt(np.mean(e*e))),bias=float(e.mean()),exact_count=int(sum(e==0)),exact_rate=float(np.mean(e==0)),
        within1_count=int(sum(abs(e)<=1)),within1_rate=float(np.mean(abs(e)<=1)),
        within2_count=int(sum(abs(e)<=2)),within2_rate=float(np.mean(abs(e)<=2)),
        abs_ge3_count=int(sum(abs(e)>=3)),abs_ge4_count=int(sum(abs(e)>=4)),over_count=int(sum(e>0)),under_count=int(sum(e<0)),
        max_over=float(e.max()),max_under=float(e.min()),
        pearson=float(pearsonr(y,p).statistic) if len(set(y))>1 and len(set(p))>1 else None,
        spearman=float(spearmanr(y,p).statistic) if len(set(y))>1 and len(set(p))>1 else None,
        quadratic_weighted_kappa=1-float((w*cm).sum())/den if den else None)


def run(test):
    test=Path(test);out=test/'011_评分对比分析';is_rubric='有得分点' in test.name
    truth_path=DATA/'011/05_全部答案_有得分.jsonl';truth=load(truth_path)
    supplied=load(test/'04_全部答案_无得分.jsonl')
    assert [{k:r[k] for k in ['answer_id','q_id','answer']} for r in truth]==supplied
    predictions=json.loads((out/'011_模型批改原始结果.json').read_text(encoding='utf-8-sig'))
    pred={r['answer_id']:r for r in predictions};assert len(pred)==len(predictions)==len(truth)==107
    rv={r['answer_id']:r for r in load(DATA/'03_待教师复核记录.jsonl')}
    rows=[]
    for r in truth:
        aid=r['answer_id'];p=pred[aid];v=rv.get(aid,{})
        status='user_approved_revision' if v.get('current_score_source')==VERSION else 'teacher_reviewed' if v.get('review_status')=='reviewed' else 'dataset_label_not_rereviewed'
        rows.append(dict(answer_id=aid,q_id='011',answer=r['answer'],reference_score=int(r['score']),reference_status=status,
            original_dataset_score=v.get('original_score',r['score']),teacher_confirmed_score=v.get('teacher_score'),
            user_approved_score=v.get('approved_score') if status=='user_approved_revision' else None,
            model_score=int(p['score']),signed_error=int(p['score'])-int(r['score']),absolute_error=abs(int(p['score'])-int(r['score'])),
            model_reason=p['reason'],answer_length=len(r['answer'].strip()),label_version=VERSION))
    a=metrics(rows);groups=defaultdict(list)
    for r in rows:groups[''.join(r['answer'].split())].append(r)
    by={str(s):metrics([r for r in rows if r['reference_score']==s]) for s in sorted({r['reference_score'] for r in rows})}
    subsets={s:metrics([r for r in rows if r['reference_status']==s]) for s in sorted({r['reference_status'] for r in rows})}
    subsets['unique_texts']=metrics([g[0] for g in groups.values()])
    thresholds={}
    for t in [6,9,10]:
        tp=sum(r['reference_score']>=t and r['model_score']>=t for r in rows)
        tn=sum(r['reference_score']<t and r['model_score']<t for r in rows)
        fp=sum(r['reference_score']<t<=r['model_score'] for r in rows)
        fn=sum(r['model_score']<t<=r['reference_score'] for r in rows)
        thresholds[str(t)]=dict(tp=tp,tn=tn,false_high=fp,false_low=fn,agreement=(tp+tn)/107)
    lengths={name:metrics([r for r in rows if lo<=r['answer_length']<=hi]) for name,lo,hi in [('0—100字',0,100),('101—300字',101,300),('301字以上',301,100000)]}
    cm=np.zeros((11,11),int)
    for r in rows:cm[r['reference_score'],r['model_score']]+=1
    meta=json.loads((out/'011_实验配置.json').read_text(encoding='utf-8'))
    meta.update(analysis_date='2026-11-10',label_version=VERSION,metrics_recomputed_without_model_rerun=True,
        reference_definition='题目答案相关数据当前有分版，分别标明教师修订、用户确认修订和未重审原标签',
        reference_sha256=hashlib.sha256(truth_path.read_bytes()).hexdigest(),truth_snapshot_sha256=hashlib.sha256(truth_path.read_bytes()).hexdigest(),teacher_reviewed_count=7,user_approved_revision_count=5,not_rereviewed_count=95,
        current_rule_file_sha256=hashlib.sha256((test/'01_题目与参考答案.md').read_bytes()).hexdigest(),
        actual_model_input_record='原模型运行时题目与答案保存在评测输入快照；当前规则文件可有后续更新，未据此重新调用模型')
    meta['input_question_snapshot_sha256']=hashlib.sha256((out/'评测输入快照/01_题目与参考答案.md').read_bytes()).hexdigest()
    stats=dict(metadata=meta,overall=a,by_reference_score=by,subgroups=subsets,by_answer_length=lengths,thresholds=thresholds,
        reference_score_counts=dict(sorted(Counter(r['reference_score'] for r in rows).items())),model_score_counts=dict(sorted(Counter(r['model_score'] for r in rows).items())),
        signed_error_counts=dict(sorted(Counter(r['signed_error'] for r in rows).items())),confusion_rows_reference_columns_model=cm.tolist())
    dump(out/'011_实验配置.json',meta);dump(out/'011_统计结果.json',stats)
    (out/'011_逐条评分对照.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    snapshot=out/'评测输入快照'/('05_人工基准分数.jsonl' if is_rubric else '011_当前基准分数快照.jsonl')
    snapshot.write_bytes(truth_path.read_bytes())
    plots(out,rows,stats,cm,is_rubric)
    report(out,rows,stats,is_rubric)
    assert len(load(out/'011_逐条评分对照.jsonl'))==107
    print(json.dumps(dict(experiment=test.name,label_version=VERSION,mae=a['mae'],bias=a['bias'],model_rerun=False),ensure_ascii=False))


def plots(out,rows,r,cm,is_rubric):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font=Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font));plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams['axes.unicode_minus']=False
    fig,axs=plt.subplots(2,2,figsize=(13,9),constrained_layout=True)
    xx=np.arange(11);y=np.array([r['reference_score'] for r in rows]);p=np.array([r['model_score'] for r in rows]);a=r['overall']
    ax=axs[0,0];ax.bar(xx-.2,np.bincount(y,minlength=11),.4,label='当前人工基准',color='#55758b');ax.bar(xx+.2,np.bincount(p,minlength=11),.4,label='保存的模型评分',color='#d58d49')
    ax.set(xticks=xx,xlabel='分数',ylabel='答案数',title='人工分数与模型分数');ax.legend(fontsize=9)
    ax=axs[0,1]
    for i in range(11):
        for j in range(11):
            if cm[i,j]:
                ax.scatter(i,j,s=25+30*cm[i,j],c='#ce8149' if j>i else '#52798f',alpha=.8,edgecolors='white')
                if cm[i,j]>=3:ax.text(i,j,str(cm[i,j]),ha='center',va='center',fontsize=8)
    ax.plot([0,10],[0,10],'--',color='#75818b');ax.set(xticks=xx,yticks=xx,xlabel='当前人工基准',ylabel='保存的模型分数',title='虚线上方：模型分数较高',xlim=(-.5,10.5),ylim=(-.5,10.5));ax.grid(alpha=.15)
    ax=axs[1,0];e=Counter(r['signed_error'] for r in rows);x=range(min(e),max(e)+1)
    ax.bar(list(x),[e[z] for z in x],color=['#55758b' if z<0 else '#77878d' if z==0 else '#d58d49' for z in x]);ax.set(xticks=list(x),xlabel='模型−人工基准',ylabel='答案数',title=f'平均有符号差{a["bias"]:+.2f}分')
    ax=axs[1,1];grades=[int(s) for s in r['by_reference_score']];means=[r['by_reference_score'][str(s)]['model_mean'] for s in grades]
    ax.plot(grades,means,'o-',color='#ce8149',label='模型平均分');ax.plot([0,10],[0,10],'--',color='#75818b',label='完全一致')
    ax.set(xticks=xx,xlabel='当前人工基准',ylabel='模型平均分',title='不同人工分档的模型表现',ylim=(-.3,10.8));ax.legend(fontsize=9)
    condition='有得分点' if is_rubric else '无得分点'
    fig.suptitle(f'011题（{condition}）｜当前基准v2026-11-10｜MAE={a["mae"]:.2f}｜±1分={a["within1_rate"]:.1%}',fontsize=14)
    fig.savefig(out/'011_评分对比图.png',dpi=160);plt.close(fig)
    matrix_path=out/'011_评分混淆矩阵.png'
    if matrix_path.exists():
        fig,ax=plt.subplots(figsize=(9,8),constrained_layout=True);im=ax.imshow(cm,cmap='Blues',origin='lower')
        for i in range(11):
            for j in range(11):
                if cm[i,j]:ax.text(j,i,str(cm[i,j]),ha='center',va='center',color='white' if cm[i,j]>cm.max()*.55 else '#203345')
        ax.set(xticks=xx,yticks=xx,xlabel='模型分数',ylabel='当前人工基准',title='011评分混淆矩阵｜2026-11-10基准');fig.colorbar(im,ax=ax)
        fig.savefig(matrix_path,dpi=160);plt.close(fig)


def report(out,rows,r,is_rubric):
    a=r['overall'];condition='有得分点' if is_rubric else '无得分点';ix={x['answer_id']:x for x in rows}
    lines=[f'# 011题：{condition}模型评分与当前人工基准\n',
        f'重算日期：2026年11月10日。基准版本：{VERSION}。模型名称按用户实验记录为DeepSeek-V4.1-Flash-1017，运行于CodeArts Agent。\n',
        '**本报告使用原先保存的107条模型输出，仅按新人工基准重新计算，不是重新运行模型。** 当前基准包括7条既有教师审核、5条用户确认修订、95条未在本项目重新核分的原标签。用户确认未冒称教师新增签字。\n',
        '## 1. 主要结论\n',
        f'人工平均分{a["reference_mean"]:.2f}，模型平均分{a["model_mean"]:.2f}；模型平均高出{a["bias"]:.2f}分。MAE为{a["mae"]:.2f}，完全同分率{a["exact_rate"]:.1%}，误差≤1分的比例{a["within1_rate"]:.1%}。这些是一致性指标，不能视为全部标签经教师重新裁定后的真实正确率。\n',
        '本次标签已按用户确认接受020、037、057的正确替代解法，并修订046、069。原来的“这些标签待定”判断已被当前确认版替代。该批案例参与了规则与标签调整，应继续视为开发/校准数据。\n',
        '![评分对比图](011_评分对比图.png)\n',
        '## 2. 数据与实验口径\n',
        '- 107个唯一答案编号全部对齐；无分输入未携带分数，答案文本与当前有分版一致，均未因本次改分而变化。',
        '- 模型原始结果、实验当时输入的题目快照保留原样。当前题目MD可能包含后续规则更新，不能倒称模型运行时看过这些新规则。',
        '- 评测输入快照中的人工基准文件已同步当前确认版；标签版本、SHA-256及复算时间写入实验配置。',
        '- 仅一次保存的模型结果；未测多次运行稳定性，未新增模型调用或费用。\n',
        '## 3. 整体一致性\n','| 指标 | 实测 |','| --- | ---: |',
        f'| 平均绝对误差MAE | {a["mae"]:.3f} |',f'| RMSE | {a["rmse"]:.3f} |',f'| 平均有符号差 | {a["bias"]:+.3f} |',
        f'| 完全同分 | {a["exact_count"]}/107，{a["exact_rate"]:.1%} |',f'| 误差≤1分 | {a["within1_count"]}/107，{a["within1_rate"]:.1%} |',
        f'| 误差≤2分 | {a["within2_count"]}/107，{a["within2_rate"]:.1%} |',f'| 绝对差≥3分 / ≥4分 | {a["abs_ge3_count"]} / {a["abs_ge4_count"]}条 |',
        f'| 高于 / 低于人工基准 | {a["over_count"]} / {a["under_count"]}条 |',f'| 最大正差 / 最大负差 | +{a["max_over"]:.0f} / {a["max_under"]:.0f} |',
        f'| Pearson / Spearman | {a["pearson"]:.3f} / {a["spearman"]:.3f} |',f'| 二次加权Kappa | {a["quadratic_weighted_kappa"]:.3f} |\n',
        '## 4. 各人工分档\n','| 人工分 | N | 模型平均分 | MAE | 平均差值 |','| ---: | ---: | ---: | ---: | ---: |']
    for s,m in r['by_reference_score'].items():lines.append(f'| {s} | {m["n"]} | {m["model_mean"]:.2f} | {m["mae"]:.2f} | {m["bias"]:+.2f} |')
    lines += ['\n各档样本量不相等，低分档部分仅有少量样本。分档均值不能直接证明模型对某类答案普遍有效或无效。\n',
        '## 5. 标签来源与重复文本\n','| 子集 | N | MAE | 完全同分率 |','| --- | ---: | ---: | ---: |']
    labels={'teacher_reviewed':'既有教师审核','user_approved_revision':'本次用户确认修订','dataset_label_not_rereviewed':'未在本项目重新审核的原标签','unique_texts':'去空白文本去重后'}
    for key,m in r['subgroups'].items():lines.append(f'| {labels[key]} | {m["n"]} | {m["mae"]:.2f} | {m["exact_rate"]:.1%} |')
    lines += ['\n7条教师案例只有两种文本；已知规则和同文样本不等于多个独立测试。用户确认的5条也参与了开发讨论，不能拿该子集单独证明模型泛化。\n',
        '## 6. 阈值一致性\n','6、9、10分为探索性界线，不代表已确认的业务验收阈值。\n',
        '| 阈值 | 人工未达→模型达到 | 人工达到→模型未达 | 一致率 |','| --- | ---: | ---: | ---: |']
    for cut,m in r['thresholds'].items():lines.append(f'| ≥{cut} | {m["false_high"]} | {m["false_low"]} | {m["agreement"]:.1%} |')
    full_n=sum(x['reference_score']==10 for x in rows);model_n=sum(x['model_score']==10 for x in rows);match=sum(x['reference_score']==x['model_score']==10 for x in rows)
    lines += [f'\n当前人工满分{full_n}条，模型满分{model_n}条，其中同时满分{match}条。其他不一致要结合评分依据核查，不仅凭标签差异断定模型错误。\n',
        '## 7. 已确认案例与剩余问题\n','| 答案编号 | 当前人工分 | 保存的模型分 | 当前结论 |','| --- | ---: | ---: | --- |']
    notes={'011-000046':'用户确认4分；保留局部正确分，不补出完整倒栈和继续出队分支。',
           '011-000069':'用户确认6分；不能借用入队循环给出队搬移分，概念分可单计。',
           '011-000020':'用户确认10分；出队后还原S1，单栈判空成立。',
           '011-000037':'用户确认10分；S2存队列，S1临时辅助，符合FIFO。',
           '011-000057':'用户确认10分；新元素置底后还原旧元素，符合FIFO。',
           '011-000038':'原文明确两栈均空，理由必须与原文一致；原标签仍待进一步审核。',
           '011-000077':'存在代码条件反向；是否识别应直接对照原文，文字与代码配分仍需统一。',
           '011-000101':'等待S2清空的约束是否支持任意交错操作，仍需复核。'}
    for aid,note in notes.items():
        x=ix[aid];lines.append(f'| {aid} | {x["reference_score"]} | {x["model_score"]} | {note} |')
    lines += ['\n逐条原文及保存的模型理由见[差异案例复核清单](011_差异案例复核清单.md)。尚未输出完整逐项分值与原文依据，不能仅因总分有理由就认定过程完全可追溯。\n',
        '## 8. 运行记录与后续使用\n']
    if is_rubric:lines+=['界面原记录为4分57秒、预估积分10.67；这是原实验的界面值，非本次复算新增费用，也不是已核验账单。\n']
    else:lines+=['原实验未记录同口径耗时与积分，本次不补造数据。\n']
    lines += ['模型输出没有重跑，指标变化来自人工标签修订。以后评估时必须记录固定标签版本；正式测试另留未参与规则或标签调整的答案。\n',
        '## 9. 文件\n',
        '- [统计结果](011_统计结果.json)、[实验配置](011_实验配置.json)、[逐条对照](011_逐条评分对照.jsonl)。',
        '- [保存的模型原始结果](011_模型批改原始结果.json)。',
        '- 题目与无分输入的历史记录保留在评测输入快照；人工基准已更新到本报告版本。\n']
    (out/'011_普通模型评分对比报告.md').write_text('\n'.join(lines),encoding='utf-8')
    case=['# 011题评分差异案例与确认状态\n',f'基准版本：{VERSION}。用户确认修订单独归因，不冒充教师新增审核；模型结果未重跑。\n']
    for aid,note in notes.items():
        x=ix[aid];case += [f'## {aid}\n',f'当前人工基准{x["reference_score"]}，模型{x["model_score"]}，分差{x["signed_error"]:+d}。\n',
            '```text',x['answer'].rstrip(),'```\n','**保存的模型理由：** '+x['model_reason']+'\n','**当前判断：** '+note+'\n']
    case += ['## 全部绝对差≥3分的记录\n','| 编号 | 人工基准 | 模型 | 分差 |','| --- | ---: | ---: | ---: |']
    for x in sorted((x for x in rows if x['absolute_error']>=3),key=lambda x:(-x['absolute_error'],x['answer_id'])):
        case.append(f'| {x["answer_id"]} | {x["reference_score"]} | {x["model_score"]} | {x["signed_error"]:+d} |')
    (out/'011_差异案例复核清单.md').write_text('\n'.join(case)+'\n',encoding='utf-8')


if __name__=='__main__':
    for name in ['正常大模型测试改卷能力(无得分点','正常大模型测试改卷能力(有得分点']:run(ROOT/name)
