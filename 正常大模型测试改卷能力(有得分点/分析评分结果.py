from pathlib import Path
from collections import Counter, defaultdict
import hashlib
import json
import re
import shutil
import numpy as np
from scipy.stats import pearsonr, spearmanr

TEST=Path(__file__).resolve().parent
ROOT=TEST.parent
OUT=TEST/'011_评分对比分析'
PASTE=Path('C:/Users/hzy/.codex/attachments/86ea7854-b2ca-4094-a83a-4cc187888e04/已粘贴的文本.txt')
NOTES={
'011-000002':'全部数据在每次出队后还原到S1，因此只判S1为空可以成立。模型的解释与该不变量一致，题干也没有效率要求；人工基准6分是否偏低，需要教师确认。',
'011-000020':'先把S1栈底以上元素移到S2，弹出栈底，再还原S1，可实现FIFO。单栈判空在操作完成后成立。模型10分、基准3分的差距不能直接认定为模型错判。',
'011-000025':'文字中“将S2出栈压入S1”是否指全部转移不清楚；该方案也可能依靠数据始终存于单侧的不变量成立。应检查任意交错操作，不能仅因是替代方案就自动给满分。',
'011-000037':'入队时把S2倒到S1、加入新元素再倒回，保证队首在S2栈顶；S1只临时使用，判S2为空成立。这是可行但入队较慢的方案，原3分值得教师复核。',
'011-000038':'学生明确写了两栈均空，模型正确识别判空，并因未明写S2非空分支给9分。基准5分尚未重新核分；应统一省略语和分支表达的配分。',
'011-000057':'把新元素放到S2旧元素之下，再倒回旧元素，可保持队首在栈顶。只检查S2为空符合此实现的不变量，基准6分需要教师解释。',
'011-000058':'原文有出队的空/非空分支，但“从S1出栈，再入S2，再出栈”没有明确全部转移。模型将其解释为完整过程，是否应满分取决于教师对省略语的口径。',
'011-000059':'学生写“把S1中的元素全部压入S1”，且未写倒栈后弹出。模型指出这些问题，给8分；教师原标签9分，需明确笔误和缺操作分别如何处理。',
'011-000062':'开头双栈概述基本正确，但正文三问用循环队列front/rear作答。模型给6分、基准0分，需要教师明确概述与实际操作冲突时如何分配部分分。',
'011-000077':'模型正确指出代码else-if反向：S2空且S1非空会报队列空，两栈均空又可能执行Pop。文字说明仍可单独得分；是否因此得8分而非基准5分，需要明确文字与代码冲突时的评分。',
'011-000101':'原文要求等S2全部出栈后才能再转入新元素，可能只说明分批处理，而未实现任意交错的入队出队。模型直接给10分，需要教师核查这种操作限制是否可接受。',
'011-000102':'原文描述了S2非空出队及两栈空报错，但缺少S1非空、S2空时的倒栈分支；模型识别该缺失。8分与基准9分的差异属于配分口径。',
'011-000030':'模型理由只写倒栈条件扣1分，却给8分；没有逐项得分，无法重建其他扣分。不能直接断言算术错，但理由不足以完整支持总分。',
'011-000033':'模型指出倒栈条件反向，这与原文相符。理由说扣1分却给8分，没有说明其他扣分项，需核查分项汇总与总分。',
}


def load(path):return [json.loads(s) for s in path.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
def dump(path,x):path.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def metrics(rows):
    y=np.array([r['reference_score'] for r in rows],float);p=np.array([r['model_score'] for r in rows],float);e=p-y
    cm=np.zeros((11,11),int)
    for a,b in zip(y,p):cm[int(a),int(b)]+=1
    w=(np.arange(11)[:,None]-np.arange(11)[None,:])**2
    den=float((w*np.outer(cm.sum(1),cm.sum(0))/len(rows)).sum())
    return dict(n=len(rows),reference_mean=float(y.mean()),model_mean=float(p.mean()),mae=float(abs(e).mean()),
        rmse=float(np.sqrt(np.mean(e*e))),bias=float(e.mean()),
        exact_count=int(sum(e==0)),exact_rate=float(np.mean(e==0)),
        within1_count=int(sum(abs(e)<=1)),within1_rate=float(np.mean(abs(e)<=1)),
        within2_count=int(sum(abs(e)<=2)),within2_rate=float(np.mean(abs(e)<=2)),
        abs_ge3_count=int(sum(abs(e)>=3)),abs_ge4_count=int(sum(abs(e)>=4)),
        over_count=int(sum(e>0)),under_count=int(sum(e<0)),max_over=float(e.max()),max_under=float(e.min()),
        pearson=float(pearsonr(y,p).statistic) if len(set(y))>1 and len(set(p))>1 else None,
        spearman=float(spearmanr(y,p).statistic) if len(set(y))>1 and len(set(p))>1 else None,
        quadratic_weighted_kappa=1-float((w*cm).sum())/den if den else None)


def main():
    OUT.mkdir(exist_ok=True)
    raw=PASTE.read_text(encoding='utf-8-sig')
    preds,_=json.JSONDecoder().raw_decode(raw[raw.index('['):])
    pred={r['answer_id']:r for r in preds}
    assert len(preds)==len(pred)==107
    truth_path=ROOT/'阅卷Agent_按题整理/011/05_全部答案_有得分.jsonl'
    truth=load(truth_path);inputs=load(TEST/'04_全部答案_无得分.jsonl')
    assert [{k:r[k] for k in ['answer_id','q_id','answer']} for r in truth]==inputs
    assert set(pred)=={r['answer_id'] for r in truth}
    rv={r['answer_id']:r for r in load(ROOT/'阅卷Agent_按题整理/03_待教师复核记录.jsonl')}
    rows=[]
    for r in truth:
        p=pred[r['answer_id']];review=rv.get(r['answer_id'],{})
        assert type(p['score']) in (int,float) and float(p['score']).is_integer() and 0<=p['score']<=10
        assert p['reason'].strip() and len(p['reason'])<=100
        rows.append(dict(answer_id=r['answer_id'],q_id='011',answer=r['answer'],reference_score=int(r['score']),
            reference_status='teacher_reviewed' if review.get('review_status')=='reviewed' else 'dataset_label_not_rereviewed',
            model_score=int(p['score']),signed_error=int(p['score'])-int(r['score']),absolute_error=abs(int(p['score'])-int(r['score'])),
            model_reason=p['reason'],answer_length=len(r['answer'].strip())))
    a=metrics(rows)
    by_score={str(s):metrics([r for r in rows if r['reference_score']==s]) for s in sorted({r['reference_score'] for r in rows})}
    groups=defaultdict(list)
    for r in rows:groups[''.join(r['answer'].split())].append(r)
    subgroups={
        'teacher_reviewed':metrics([r for r in rows if r['reference_status']=='teacher_reviewed']),
        'not_rereviewed':metrics([r for r in rows if r['reference_status']!='teacher_reviewed']),
        'unique_texts':metrics([g[0] for g in groups.values()])}
    lengths={label:metrics([r for r in rows if lo<=r['answer_length']<=hi]) for label,lo,hi in [('0—100字',0,100),('101—300字',101,300),('301字以上',301,100000)]}
    thresholds={}
    for cut in [6,9,10]:
        tp=sum(r['reference_score']>=cut and r['model_score']>=cut for r in rows)
        tn=sum(r['reference_score']<cut and r['model_score']<cut for r in rows)
        fp=sum(r['reference_score']<cut<=r['model_score'] for r in rows)
        fn=sum(r['model_score']<cut<=r['reference_score'] for r in rows)
        thresholds[str(cut)]=dict(tp=tp,tn=tn,false_high=fp,false_low=fn,agreement=(tp+tn)/107)
    rng=np.random.default_rng(20261108);cluster=list(groups.values());boot=[]
    for _ in range(4000):
        picks=rng.integers(0,len(cluster),len(cluster));e=np.array([r['signed_error'] for i in picks for r in cluster[int(i)]],float)
        boot.append([abs(e).mean(),e.mean(),np.mean(abs(e)<=1)])
    ci={key:[float(v) for v in np.quantile(np.array(boot)[:,i],[.025,.975])] for i,key in enumerate(['mae','bias','within1_rate'])}
    cm=np.zeros((11,11),int)
    for r in rows:cm[r['reference_score'],r['model_score']]+=1
    metadata=dict(date='2026-11-08',model_name_user_reported='DeepSeek-V4.1-Flash-1017',platform='CodeArts Agent',
        condition='提供题目、参考答案、得分点与建议配分，直接给出总分和短理由',
        reference_definition='整理包当前05_全部答案_有得分.jsonl中的人工分数，包含已确认的教师修订，不回退至下载时的旧标签',
        scored_samples_explicitly_in_prompt_not_verified=True,
        rule_contains_teacher_9_point_anchors=True,runs=1,temperature=None,actual_model_call_count=None,
        displayed_completion_seconds=297,estimated_credits_from_ui=10.67,
        answer_input_matches_reference_verified=True,reason_over_100_characters=0,
        reference_sha256=hashlib.sha256(truth_path.read_bytes()).hexdigest(),
        input_sha256=hashlib.sha256((TEST/'04_全部答案_无得分.jsonl').read_bytes()).hexdigest())
    result=dict(metadata=metadata,overall=a,by_reference_score=by_score,subgroups=subgroups,by_answer_length=lengths,
        thresholds=thresholds,cluster_bootstrap_95_ci=ci,confusion_rows_reference_columns_model=cm.tolist(),
        reference_score_counts=dict(sorted(Counter(r['reference_score'] for r in rows).items())),
        model_score_counts=dict(sorted(Counter(r['model_score'] for r in rows).items())),
        signed_error_counts=dict(sorted(Counter(r['signed_error'] for r in rows).items())))
    shutil.copyfile(PASTE,OUT/'模型原始粘贴.txt')
    dump(OUT/'011_模型批改原始结果.json',preds)
    dump(OUT/'011_实验配置.json',metadata);dump(OUT/'011_统计结果.json',result)
    (OUT/'011_逐条评分对照.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    snapshot=OUT/'评测输入快照';snapshot.mkdir(exist_ok=True)
    for p in [TEST/'01_题目与参考答案.md',TEST/'04_全部答案_无得分.jsonl']:
        shutil.copyfile(p,snapshot/p.name)
    shutil.copyfile(truth_path,snapshot/'05_人工基准分数.jsonl')
    charts(rows,result,cm)
    reports(rows,result)
    assert len(load(OUT/'011_逐条评分对照.jsonl'))==107
    for p in OUT.glob('*.md'):
        content=p.read_text(encoding='utf-8')
        for link in re.findall(r'\]\(([^)]+)\)',content):
            if not link.startswith('http'):assert (p.parent/link).exists()
    print(json.dumps(dict(output=str(OUT),metrics=a,verified='single_run_only'),ensure_ascii=False))


def charts(rows,result,cm):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font=Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font));plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams['axes.unicode_minus']=False
    fig,axs=plt.subplots(2,2,figsize=(13,9),constrained_layout=True)
    xs=np.arange(11);y=np.array([r['reference_score'] for r in rows]);p=np.array([r['model_score'] for r in rows])
    ax=axs[0,0]
    ax.bar(xs-.2,np.bincount(y,minlength=11),.4,label='人工基准',color='#55758b')
    ax.bar(xs+.2,np.bincount(p,minlength=11),.4,label='模型评分',color='#d58d49')
    ax.set(xticks=xs,xlabel='分数',ylabel='答案数',title='人工分数与模型分数分布');ax.legend()
    ax=axs[0,1]
    for i in range(11):
        for j in range(11):
            if cm[i,j]:
                ax.scatter(i,j,s=25+30*cm[i,j],c='#ce8149' if j>i else '#52798f',alpha=.8,edgecolors='white')
                if cm[i,j]>=3:ax.text(i,j,str(cm[i,j]),ha='center',va='center',fontsize=8)
    ax.plot([0,10],[0,10],'--',color='#75818b');ax.set(xticks=xs,yticks=xs,xlabel='人工基准分数',ylabel='模型分数',title='虚线上方：模型高于人工基准',xlim=(-.5,10.5),ylim=(-.5,10.5));ax.grid(alpha=.15)
    ax=axs[1,0];errors=Counter(r['signed_error'] for r in rows);xx=range(-3,8)
    ax.bar(list(xx),[errors[x] for x in xx],color=['#55758b' if x<0 else '#77878d' if x==0 else '#d58d49' for x in xx])
    ax.set(xticks=list(xx),xlabel='模型分数−人工基准分数',ylabel='答案数',title='分差分布：平均高出1.12分')
    ax=axs[1,1]
    grades=[int(k) for k in result['by_reference_score']]
    means=[result['by_reference_score'][str(i)]['model_mean'] for i in grades]
    ax.plot(grades,means,'o-',color='#ce8149',label='模型平均分');ax.plot([0,10],[0,10],'--',color='#75818b',label='完全一致')
    for i,z in zip(grades,means):ax.annotate('n='+str(result['by_reference_score'][str(i)]['n']),(i,z),xytext=(0,7),textcoords='offset points',ha='center',fontsize=8)
    ax.set(xticks=xs,xlabel='人工基准分数',ylabel='模型平均分',title='不同人工分档对应的模型平均分',ylim=(-.3,10.8));ax.legend(fontsize=8)
    fig.suptitle('011题：有得分点评分与人工基准｜N=107｜MAE=1.51｜±1分一致率=65.4%',fontsize=15)
    fig.savefig(OUT/'011_评分对比图.png',dpi=160);plt.close(fig)


def reports(rows,r):
    a=r['overall'];ci=r['cluster_bootstrap_95_ci'];by=r['by_reference_score'];sub=r['subgroups']
    lines=['# 011题：有得分点的大模型评分与人工分数比较\n','分析日期：2026年11月8日。模型按用户记录为DeepSeek-V4.1-Flash-1017，运行于CodeArts Agent。本报告仅分析本次提供得分点的评分结果。\n',
        '## 1. 比较口径与结论\n',
        '**本报告的“原始分数/人工基准”指整理包当前有分JSONL中的人工分数，包含老师已确认的修订，并非回退到下载时的旧标签。** 当前107条中有7条经过本项目教师明确复核，其余100条仍是原数据人工标签。\n',
        f'107条模型结果完整对齐。模型平均分{a["model_mean"]:.2f}，人工基准平均分{a["reference_mean"]:.2f}；平均高出{a["bias"]:.2f}分。平均绝对误差为{a["mae"]:.2f}分，完全同分率{a["exact_rate"]:.1%}，误差≤1分的一致率{a["within1_rate"]:.1%}。模型对中低分答案给分更宽，对高分答案更贴近人工标签。\n',
        '部分大分差来自可行替代算法与原标签之间的争议；另有省略步骤、文字代码冲突及理由不足以还原总分的问题。因此完全同分率不是已经过教师重新裁定的真实正确率，不能把全部分差直接算成模型错判。\n',
        '![评分对比图](011_评分对比图.png)\n',
        '## 2. 数据与输出检查\n',
        '- 107个唯一答案编号，无缺失、重复或额外编号；输入只含answer_id、q_id、answer，未含分数，并与人工基准的答案文本逐条一致。',
        '- 107条模型分数均为0—10整数，理由均非空且不超过100个字符。题目MD含满分、参考答案、五个得分点与判定说明。',
        '- 只有一次评分结果；未获得完整实际提示词、温度、内部拆批、调用日志，不能评价多次运行稳定性。\n',
        '## 3. 整体分数一致性\n','| 指标 | 实测结果 |','| --- | ---: |',
        f'| 人工均分 / 模型均分 | {a["reference_mean"]:.2f} / {a["model_mean"]:.2f} |',
        f'| 平均绝对误差MAE | {a["mae"]:.3f}分 |',
        f'| 均方根误差RMSE | {a["rmse"]:.3f}分 |',
        f'| 平均有符号差 | +{a["bias"]:.3f}分 |',
        f'| 完全同分 | {a["exact_count"]}/107，{a["exact_rate"]:.1%} |',
        f'| 误差≤1分 | {a["within1_count"]}/107，{a["within1_rate"]:.1%} |',
        f'| 误差≤2分 | {a["within2_count"]}/107，{a["within2_rate"]:.1%} |',
        f'| 绝对差≥3分 | {a["abs_ge3_count"]}/107 |',
        f'| 绝对差≥4分 | {a["abs_ge4_count"]}/107 |',
        f'| 高于 / 低于人工基准 | {a["over_count"]} / {a["under_count"]}条 |',
        f'| 最大正差 / 最大负差 | +{a["max_over"]:.0f} / {a["max_under"]:.0f}分 |',
        f'| Pearson / Spearman | {a["pearson"]:.3f} / {a["spearman"]:.3f} |',
        f'| 二次加权Kappa | {a["quadratic_weighted_kappa"]:.3f} |\n',
        f'按98种去空白文本进行4,000次组级bootstrap，MAE的95%区间约为[{ci["mae"][0]:.2f}, {ci["mae"][1]:.2f}]。该区间只描述本批样本抽样不确定性，不包含标签错误与模型重复运行波动。\n',
        '## 4. 各人工分档表现\n','| 人工分数 | N | 模型平均分 | MAE | 平均差值 |','| ---: | ---: | ---: | ---: | ---: |']
    for grade,m in by.items():lines.append(f'| {grade} | {m["n"]} | {m["model_mean"]:.2f} | {m["mae"]:.2f} | {m["bias"]:+.2f} |')
    lines += ['\n原3分和4分组的模型平均分分别为8.0和8.2，分差尤其突出；但两组各仅5条，其中含有替代算法争议，不能据此直接认定模型在低分答案上全部判断错误。\n',
        '## 5. 阈值与满分判断\n','6、9、10分只作为探索性界线，不代表已确定的业务验收标准。\n',
        '| 阈值 | 人工未达→模型达到 | 人工达到→模型未达 | 阈值一致率 |','| --- | ---: | ---: | ---: |']
    for cut,m in r['thresholds'].items():lines.append(f'| ≥{cut} | {m["false_high"]} | {m["false_low"]} | {m["agreement"]:.1%} |')
    lines += ['\n人工满分18条，模型满分31条；模型满分中14条人工也满分、17条人工低于满分，另有4条人工满分被模型给了较低分。满分与人工标签匹配14/31=45.2%，但17条不一致仍需逐项复核，不能直接等同17次已确认错判。\n',
        '## 6. 教师复核、重复文本与长度\n','| 子集 | N | MAE | 平均差值 | 完全同分率 |','| --- | ---: | ---: | ---: | ---: |']
    for key,label in [('teacher_reviewed','老师已明确复核'),('not_rereviewed','未重新核分的原标签'),('unique_texts','同文去重后')]:
        m=sub[key];lines.append(f'| {label} | {m["n"]} | {m["mae"]:.2f} | {m["bias"]:+.2f} | {m["exact_rate"]:.1%} |')
    lines += ['\n老师已复核7条均为9分，模型也给9分，但仅对应两种文本；输入细则已经注明这两类表述为9分，因此该结果反映已知规则遵从，不构成未见答案的独立验证。5个重复文本组内模型分数均一致，只说明本次任务内的一致性。\n',
        '| 答案字符数 | N | MAE | 平均差值 |','| --- | ---: | ---: | ---: |']
    for label,m in r['by_answer_length'].items():lines.append(f'| {label} | {m["n"]} | {m["mae"]:.2f} | {m["bias"]:+.2f} |')
    lines += ['\n字符数不等于token数。不同长度组的题解与人工分数构成不同，本表不能用于因果判断模型偏好长答案。\n',
        '## 7. 理由可靠性与重点案例\n','| 答案编号 | 人工基准 | 模型 | 需要核查的方面 |','| --- | ---: | ---: | --- |']
    lookup={r['answer_id']:r for r in rows}
    for aid,note in NOTES.items():
        r0=lookup[aid];lines.append(f'| {aid} | {r0["reference_score"]} | {r0["model_score"]} | {note.split("。")[0]}。 |')
    lines += ['\n完整原文、理由和初步分析见[差异案例复核清单](011_差异案例复核清单.md)。模型已能识别一些真实代码缺陷和替代解法，但仍需核查：是否替学生补全了未写操作、是否检查任意交错操作、是否把正确概述与错误代码区分给分。\n',
        '输出只有总分和短理由，没有逐项分值或原文引用，因此不能完整检验分项合计、逐点准确率与证据支持率。理由说“扣1分/扣2分”但没有列其余项时，只能说可追溯信息不足，不能直接断言算术错误。\n',
        '## 8. 时间、积分与实验限制\n',
        '- 界面显示完成时间4分57秒，即297秒；预估积分10.67。积分是预估值，不是实际账单金额；完成时间也不等于已核验的纯模型推理时间。',
        '- 模型分数可用于评分建议，但目前的人工基准尚未全部复核，细则也引用了同批案例。该批数据属于开发评测资料，不能直接用来声称未见测试的泛化效果。',
        '- 建议先请教师确认替代算法、概述与代码冲突、笔误和省略步骤的处理，再冻结独立测试标签。需要另外记录教师准备、复核、返工总工时，才能评估实际使用价值。\n',
        '## 9. 数据文件\n',
        '- [模型原始结果](011_模型批改原始结果.json)、[原始粘贴](模型原始粘贴.txt)。',
        '- [逐条评分对照](011_逐条评分对照.jsonl)：107条，含学生原文、人工分、模型分、分差、理由与基准审核状态。',
        '- [统计结果](011_统计结果.json)、[实验配置](011_实验配置.json)。',
        '- 本次题目、无分答案与人工基准文件保存在`评测输入快照`。输入答案与人工分数均未修改。\n']
    (OUT/'011_普通模型评分对比报告.md').write_text('\n'.join(lines),encoding='utf-8')
    case_lines=['# 011题：模型评分与人工分数的差异案例\n','本清单只核查本次有得分点的模型输出，不更改原分。人工基准取整理包当前有分版，含教师确认修订。\n']
    for aid,note in NOTES.items():
        x=lookup[aid]
        case_lines += [f'## {aid}\n',f'人工基准{x["reference_score"]}；模型{x["model_score"]}；分差{x["signed_error"]:+d}。\n',
            '**学生原文：**\n','```text',x['answer'].rstrip(),'```\n','**模型理由：** '+x['model_reason']+'\n',
            '**初步分析：** '+note+'\n','**教师裁定：** 确认分数______；理由______。\n']
    case_lines += ['## 全部绝对差≥3分的记录\n','| 编号 | 人工基准 | 模型 | 分差 |','| --- | ---: | ---: | ---: |']
    for x in sorted((x for x in rows if x['absolute_error']>=3),key=lambda x:(-x['absolute_error'],x['answer_id'])):
        case_lines.append(f'| {x["answer_id"]} | {x["reference_score"]} | {x["model_score"]} | {x["signed_error"]:+d} |')
    (OUT/'011_差异案例复核清单.md').write_text('\n'.join(case_lines)+'\n',encoding='utf-8')


if __name__=='__main__':main()
