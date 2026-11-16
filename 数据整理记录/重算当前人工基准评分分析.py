"""Recompute saved model outputs against the active label version; no model call."""
from pathlib import Path
from collections import Counter, defaultdict
import hashlib
import json
import numpy as np
from scipy.stats import pearsonr,spearmanr

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'题目答案相关数据'
APPROVAL=ROOT/'A全面预处理_2026-11-16/数据/执行摘要.json'
VERSION=json.loads(APPROVAL.read_text(encoding='utf-8'))['label_version']
ANALYSIS_DATE='2026-11-16'
CHANGE_IDS={r['answer_id'] for r in json.loads((ROOT/'A全面预处理_2026-11-16/数据/实际改分清单.json').read_text(encoding='utf-8'))}


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
        status='user_approved_review' if v.get('user_approved') is True else 'teacher_reviewed' if v.get('review_status')=='reviewed' else 'dataset_label_not_rereviewed'
        rows.append(dict(answer_id=aid,q_id='011',answer=r['answer'],reference_score=int(r['score']),reference_status=status,
            original_dataset_score=v.get('original_score',r['score']),teacher_confirmed_score=v.get('teacher_score'),
            user_approved_score=v.get('approved_score') if status=='user_approved_review' else None,
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
    meta.update(analysis_date=ANALYSIS_DATE,label_version=VERSION,metrics_recomputed_without_model_rerun=True,
        reference_definition='当前组内确认基准；用户确认与历史教师总分分别记录，不冒称新增教师逐项审核',
        reference_sha256=hashlib.sha256(truth_path.read_bytes()).hexdigest(),truth_snapshot_sha256=hashlib.sha256(truth_path.read_bytes()).hexdigest(),
        teacher_reviewed_count=sum(x['teacher_confirmed_score'] is not None for x in rows),
        user_approved_revision_count=sum(x['answer_id'] in CHANGE_IDS for x in rows),
        user_approved_review_count=sum(x['reference_status']=='user_approved_review' for x in rows),
        not_rereviewed_count=sum(x['reference_status']=='dataset_label_not_rereviewed' for x in rows),
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
    fig.suptitle(f'011题（{condition}）｜{VERSION}｜MAE={a["mae"]:.2f}｜±1分={a["within1_rate"]:.1%}',fontsize=12)
    fig.savefig(out/'011_评分对比图.png',dpi=160);plt.close(fig)
    matrix_path=out/'011_评分混淆矩阵.png'
    if matrix_path.exists():
        fig,ax=plt.subplots(figsize=(9,8),constrained_layout=True);im=ax.imshow(cm,cmap='Blues',origin='lower')
        for i in range(11):
            for j in range(11):
                if cm[i,j]:ax.text(j,i,str(cm[i,j]),ha='center',va='center',color='white' if cm[i,j]>cm.max()*.55 else '#203345')
        ax.set(xticks=xx,yticks=xx,xlabel='模型分数',ylabel='当前确认基准',title=f'011评分混淆矩阵｜{VERSION}');fig.colorbar(im,ax=ax)
        fig.savefig(matrix_path,dpi=160);plt.close(fig)


def report(out,rows,r,is_rubric):
    a=r['overall'];meta=r['metadata'];condition='有得分点' if is_rubric else '无得分点'
    approved=meta['user_approved_review_count'];unreviewed=meta['not_rereviewed_count']
    lines=[f'# 011题：{condition}模型评分与当前组内确认基准\n',
        f'重算日期：{ANALYSIS_DATE}；标签集：`{VERSION}`。\n',
        '**本报告复用原先保存的107条模型输出，更新参考标签后重新计算，没有新模型调用。**\n',
        f'本题107条中，{approved}条经过本轮用户确认审阅，{unreviewed}条仍沿用未重新核分的原标签。'
        f'另有{meta["teacher_reviewed_count"]}条保留历史教师总分记录；这些统计可能重叠，不能相加充作样本数。'
        '没有把用户确认冒称新增教师签字。\n',
        '## 整体一致性\n','| 指标 | 当前值 |','| --- | ---: |',
        f'| 平均绝对误差MAE | {a["mae"]:.6f} |',f'| RMSE | {a["rmse"]:.6f} |',
        f'| 平均有符号差 | {a["bias"]:+.6f} |',f'| 完全同分 | {a["exact_count"]}/107 |',
        f'| 误差≤1分 | {a["within1_count"]}/107，{a["within1_rate"]:.1%} |',
        f'| 误差≤2分 | {a["within2_count"]}/107，{a["within2_rate"]:.1%} |',
        f'| 绝对差≥3 / ≥4分 | {a["abs_ge3_count"]} / {a["abs_ge4_count"]}条 |',
        f'| 高于 / 低于参考分 | {a["over_count"]} / {a["under_count"]}条 |',
        f'| Pearson / Spearman | {a["pearson"]:.3f} / {a["spearman"]:.3f} |',
        f'| 二次加权Kappa | {a["quadratic_weighted_kappa"]:.3f} |\n',
        '![评分对比图](011_评分对比图.png)\n',
        '这些指标是保存输出与当前组内标签的一致性。部分标签在查看模型分歧后修订，样本也已用于开发，'
        '不能据此声称模型重新运行后更准、真实教师校准收益或独立最终测试效果。\n',
        '## 数据与版本\n',
        '- 107个编号全部对齐，答案原文不变。',
        '- 原始模型输出、实际运行时的题目/无分答案快照保持原样；当前规则可能比当时新，不能倒称模型看过新规则。',
        '- 评测用参考分快照同步当前标签，标签和SHA256写入实验配置。',
        '- 最新逐项确认与29条改分依据见[数据进一步处理](../../数据进一步处理.md)。\n',
        '## 各参考分档\n','| 参考分 | N | 模型均分 | MAE | 平均差 |','| ---: | ---: | ---: | ---: | ---: |']
    for s,m in r['by_reference_score'].items():lines.append(f'| {s} | {m["n"]} | {m["model_mean"]:.2f} | {m["mae"]:.2f} | {m["bias"]:+.2f} |')
    lines+=['\n## 互斥来源子集及同文去重\n','| 子集 | N | MAE | 完全同分率 |','| --- | ---: | ---: | ---: |']
    labels={'teacher_reviewed':'仅历史教师总分审核','user_approved_review':'本轮用户确认审阅','dataset_label_not_rereviewed':'沿用原数据集标签','unique_texts':'去空白文本去重后'}
    for k,m in r['subgroups'].items():lines.append(f'| {labels.get(k,k)} | {m["n"]} | {m["mae"]:.3f} | {m["exact_rate"]:.1%} |')
    lines+=['\n同文组不是多个独立测试；原学习样本和移出的历史学习样本仍应排除，当前材料均按开发用途处理。\n',
        '## 探索性阈值\n','6、9、10分只是分档分析，不代表正式业务合格阈值。\n',
        '| 阈值 | 参考未达→模型达到 | 参考达到→模型未达 | 一致率 |','| --- | ---: | ---: | ---: |']
    for cut,m in r['thresholds'].items():lines.append(f'| ≥{cut} | {m["false_high"]} | {m["false_low"]} | {m["agreement"]:.1%} |')
    lines+=['\n## 运行信息与文件\n','模型名称、原运行平台、耗时和费用继续以原实验记录为准，本次不新填运行事实。',
        '统计数据：[统计结果](011_统计结果.json)、[实验配置](011_实验配置.json)、[逐条对照](011_逐条评分对照.jsonl)。',
        '原始结果：[保存的模型输出](011_模型批改原始结果.json)。',
        '案例：[当前分差与确认依据](011_差异案例复核清单.md)。\n']
    (out/'011_普通模型评分对比报告.md').write_text('\n'.join(lines),encoding='utf-8')
    decisions={x['answer_id']:x for x in load(ROOT/'A全面预处理_2026-11-16/数据/全部疑点逐项预处理.jsonl')}
    chosen=CHANGE_IDS | {x['answer_id'] for x in rows if x['absolute_error']>=3} | {'011-000083','011-000102','011-000053'}
    case=['# 011当前分差与用户确认依据\n',f'标签集：`{VERSION}`。保存的模型输出没有重跑。\n',
          '| 编号 | 当前参考 | 模型 | 模型−参考 | 状态 |','| --- | ---: | ---: | ---: | --- |']
    for x in sorted((x for x in rows if x['answer_id'] in chosen),key=lambda x:(-x['absolute_error'],x['answer_id'])):
        case.append(f'| {x["answer_id"]} | {x["reference_score"]} | {x["model_score"]} | {x["signed_error"]:+d} | {x["reference_status"]} |')
    for x in sorted((x for x in rows if x['answer_id'] in chosen),key=lambda x:x['answer_id']):
        decision=decisions.get(x['answer_id'])
        note=decision['decision_reason'] if decision else '本轮尚未重新逐项判分，保留原标签；分差不等于任一方已被证明错误。'
        case += [f'\n## {x["answer_id"]}\n',f'当前参考{x["reference_score"]}，保存模型{x["model_score"]}，分差{x["signed_error"]:+d}。\n',
                 '```text',x['answer'].rstrip(),'```\n','**用户确认的处理依据：** '+note+'\n','**保存的模型理由：** '+x['model_reason']+'\n']
    (out/'011_差异案例复核清单.md').write_text('\n'.join(case)+'\n',encoding='utf-8')


if __name__=='__main__':
    for name in ['正常大模型测试改卷能力(无得分点','正常大模型测试改卷能力(有得分点']:run(ROOT/name)
