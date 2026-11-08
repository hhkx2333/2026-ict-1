from pathlib import Path
from collections import defaultdict, Counter, deque
from itertools import product
import hashlib
import json
import math
import shutil
import re
import numpy as np
from scipy.stats import pearsonr, spearmanr

ROOT = Path(__file__).resolve().parent.parent
TEST = Path(__file__).resolve().parent
OUT = TEST / '011_评分对比分析'
ATTACHMENT = Path('C:/Users/hzy/.codex/attachments/4421f3a1-2144-429a-9f77-ae6ee5964fff/已粘贴的文本.txt')
PROMPT = '''你是一名数据结构课程阅卷教师。请根据题目、参考答案(参见文件夹里的"01_题目与参考答案.md"),对"04_全部答案_无得分.jsonl"里面的每个答案给出你的评分,仅返回以下JSON：
{
  "answer_id": "答案编号",
  "score": 0,
  "reason": "简要说明得分依据、遗漏或错误，不超过100字"
}'''

MANUAL = [
    ('011-000037','原标签值得复核，不能直接认定模型多给7分',
     '该方案把全部数据保留在S2，S1只临时倒腾。入队先把S2倒入S1，加入新元素后倒回S2；最早进入的元素回到S2栈顶。每次操作完成后S1为空，所以只判S2即可。这是正确但入队O(n)的替代方案，题干未要求摊还O(1)。原3分的合理性需教师确认，模型认可方案本身有依据。'),
    ('011-000020','原标签值得复核，不能直接认定模型多给6分',
     '全部元素保存在S1，最早元素在栈底。出队先移走其上元素，弹出栈底，再把临时移出的元素倒回；每次操作后S2为空，判S1为空成立。低效率不等于队列语义错误；是否必须写空队列保护、效率要求以及如何配分，应由教师明确。'),
    ('011-000057','替代方案有效，原6分需解释',
     '入队时先把S2原有元素倒入S1，将新元素压入S2底部，再倒回旧元素，使最早元素位于栈顶；出队直接弹S2，只判S2即可。这与原参考的输入栈/输出栈持久分工不同，但队列语义可以成立。'),
    ('011-000038','模型理由与学生原文直接矛盾',
     '模型说“未说明判空需两栈均空”，但学生第3条明确写“s1与s2皆为空”。这条扣分理由不成立；原5分也不因此自动正确，因为倒栈条件仍可能不完整。此例证明的是理由读错，不是确定最终应给几分。'),
    ('011-000077','模型漏掉真实代码分支错误',
     '模型称“三段算法均正确”，实际DeQueue中else if(!StackEmpty(S1))在S1非空且S2为空时报“队列为空”。例如S1=[1]、S2=[]时本应倒栈出队，却报空；两栈均空又进入后面的else并尝试Pop。相同类型的错误在011-000041被模型识别，本条却漏掉，显示代码检查不一致。文字思路可另外给分，但不能称代码均正确。'),
    ('011-000002','替代算法认可与扣分理由存在冲突',
     '学生明确说出队后把S2剩余元素全部压回S1，因而操作结束后只判S1是正确的。模型一方面认可倒回方案，另一方面以“未说明两栈均空”扣分，套用了原参考的固定模板。题干没有效率要求，是否因O(n)出队扣分也需明确。模型9与基准6的差异不能单方面归错于模型。'),
    ('011-000059','对笔误的容忍口径不同',
     '学生把“全部压入S2”写成“全部压入S1”，模型识别到了该笔误并给6分，基准给9分。是否按可推断意图认可、以及应扣几分需要教师确定；这既可能是模型过严，也可能是原标签过宽，不能只看数值定输赢。'),
    ('011-000089','模型确实识别到代码不可达问题',
     'Dequeue在S2空时直接return false，后面的倒栈逻辑不会执行。模型指出这一点与原文相符；它给7分、基准给5分主要涉及正确入队、判空与错误出队之间的配分。这个例子说明模型具有实际错误识别能力。'),
    ('011-000058','模型可能补全了没有明确写出的操作',
     '原文有S2空/非空两分支和两栈判空，但空时仅写“从s1出栈，再入栈到s2，再出栈”，没有明确全部逐个转移。模型给满分可能采用宽松解释；原4分也需结合教师对省略语的口径重新确认。'),
    ('011-000019','入队分支有遗漏，模型给满分需要复核',
     '入队若S2不空，只写把S2旧数据倒入S1；若S2为空才写加入新元素。若按两个互斥分支理解，前一分支漏了新元素入栈。模型把它概括成完整保序入队，可能替学生补全了操作；若学生原意是先清空S2后再执行第二个if，则又可能成立。应由教师确认文字解释。'),
]


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def metrics(rows):
    y=np.array([r['reference_score'] for r in rows],dtype=float)
    p=np.array([r['model_score'] for r in rows],dtype=float)
    e=p-y
    cm=np.zeros((11,11),dtype=int)
    for a,b in zip(y,p):cm[int(a),int(b)]+=1
    w=(np.arange(11)[:,None]-np.arange(11)[None,:])**2
    expected=np.outer(cm.sum(axis=1),cm.sum(axis=0))/len(rows)
    denom=float((w*expected).sum())
    return dict(n=len(rows),reference_mean=float(y.mean()),model_mean=float(p.mean()),
        mae=float(np.mean(abs(e))),rmse=float(np.sqrt(np.mean(e**2))),bias=float(e.mean()),
        exact_count=int(sum(e==0)),exact_rate=float(np.mean(e==0)),
        within1_count=int(sum(abs(e)<=1)),within1_rate=float(np.mean(abs(e)<=1)),
        within2_count=int(sum(abs(e)<=2)),within2_rate=float(np.mean(abs(e)<=2)),
        within3_count=int(sum(abs(e)<=3)),within3_rate=float(np.mean(abs(e)<=3)),
        over_count=int(sum(e>0)),under_count=int(sum(e<0)),
        abs_ge3_count=int(sum(abs(e)>=3)),abs_ge4_count=int(sum(abs(e)>=4)),
        max_over=float(e.max()),max_under=float(e.min()),
        pearson=float(pearsonr(y,p).statistic) if len(set(y))>1 and len(set(p))>1 else None,
        spearman=float(spearmanr(y,p).statistic) if len(set(y))>1 and len(set(p))>1 else None,
        quadratic_weighted_kappa=1-float((w*cm).sum())/denom if denom else None)


def percentage(value):return f'{100*value:.1f}%'


def check_alternatives():
    # Own interpretations of the written algorithms, not execution of student code.
    results={}
    for kind in ['restore_s1','persistent_s2']:
        checked=0
        for n in range(1,11):
            for ops in product('ED',repeat=n):
                balance=0
                for op in ops:
                    balance += 1 if op=='E' else -1
                    if balance<0:break
                else:
                    s1=[];s2=[];expected=deque();serial=0
                    for op in ops:
                        if op=='E':
                            serial+=1;expected.append(serial)
                            if kind=='restore_s1':s1.append(serial)
                            else:
                                while s2:s1.append(s2.pop())
                                s1.append(serial)
                                while s1:s2.append(s1.pop())
                        else:
                            if kind=='restore_s1':
                                while len(s1)>1:s2.append(s1.pop())
                                actual=s1.pop()
                                while s2:s1.append(s2.pop())
                            else:actual=s2.pop()
                            assert actual==expected.popleft()
                        assert (not expected)==(not s1 if kind=='restore_s1' else not s2)
                    checked+=1
        results[kind]=dict(legal_operation_sequences_checked=checked,max_operations=10,matched_fifo=True)
    return results


def main():
    OUT.mkdir(exist_ok=True)
    snapshot=OUT/'评测输入快照';snapshot.mkdir(exist_ok=True)
    source=ROOT/'阅卷Agent_按题整理/011/05_全部答案_有得分.jsonl'
    review_path=ROOT/'阅卷Agent_按题整理/03_待教师复核记录.jsonl'
    learning_path=ROOT/'阅卷Agent_按题整理/011/03_学习样本10%_有得分.jsonl'
    truth=read_jsonl(source)
    supplied=read_jsonl(TEST/'04_全部答案_无得分.jsonl')
    predictions=json.loads(ATTACHMENT.read_text(encoding='utf-8-sig'))
    assert len(predictions)==len(truth)==len(supplied)==107
    assert len({r['answer_id'] for r in predictions})==107
    assert all(set(r)=={'answer_id','q_id','answer'} for r in supplied)
    assert [{k:r[k] for k in ['answer_id','q_id','answer']} for r in truth]==supplied
    pred={r['answer_id']:r for r in predictions}
    assert set(pred)=={r['answer_id'] for r in truth}
    assert all(type(r['score']) in (int,float) and 0<=r['score']<=10 and float(r['score']).is_integer() and isinstance(r['reason'],str) and r['reason'].strip() for r in predictions)
    reviewed={r['answer_id']:r for r in read_jsonl(review_path)}
    learning={r['answer_id'] for r in read_jsonl(learning_path)}
    rows=[]
    for r in truth:
        aid=r['answer_id'];p=pred[aid];rv=reviewed.get(aid,{})
        rows.append(dict(answer_id=aid,q_id='011',answer=r['answer'],reference_score=int(r['score']),
            reference_status='teacher_reviewed' if rv.get('review_status')=='reviewed' else 'dataset_label_not_rereviewed',
            original_dataset_score=rv.get('original_score',r['score']),teacher_confirmed_score=rv.get('teacher_score'),
            model_score=int(p['score']),signed_error=int(p['score'])-int(r['score']),absolute_error=abs(int(p['score'])-int(r['score'])),
            model_reason=p['reason'],in_existing_learning_sample=aid in learning,
            answer_length=len(r['answer'].strip()),model_reason_length=len(p['reason'])))
    all_stats=metrics(rows)
    by_score={str(s):metrics([r for r in rows if r['reference_score']==s]) for s in sorted({r['reference_score'] for r in rows})}
    subsets={
        'teacher_reviewed':metrics([r for r in rows if r['reference_status']=='teacher_reviewed']),
        'not_rereviewed':metrics([r for r in rows if r['reference_status']!='teacher_reviewed']),
        'existing_learning_subset':metrics([r for r in rows if r['in_existing_learning_sample']]),
        'remaining_subset':metrics([r for r in rows if not r['in_existing_learning_sample']]),
    }
    grouped=defaultdict(list)
    for r in rows:grouped[''.join(r['answer'].split())].append(r)
    unique_rows=[g[0] for g in grouped.values()]
    assert all(len({r['reference_score'] for r in g})==1 for g in grouped.values())
    duplicate_groups=[g for g in grouped.values() if len(g)>1]
    duplicate_consistent=sum(len({r['model_score'] for r in g})==1 for g in duplicate_groups)
    subsets['unique_texts']=metrics(unique_rows)
    lengths={}
    for label,low,high in [('0—100字',0,100),('101—300字',101,300),('301字以上',301,100000)]:
        rr=[r for r in rows if low<=r['answer_length']<=high]
        lengths[label]=metrics(rr)
    thresholds={}
    for threshold in [6,9,10]:
        tp=sum(r['reference_score']>=threshold and r['model_score']>=threshold for r in rows)
        tn=sum(r['reference_score']<threshold and r['model_score']<threshold for r in rows)
        fp=sum(r['reference_score']<threshold and r['model_score']>=threshold for r in rows)
        fn=sum(r['reference_score']>=threshold and r['model_score']<threshold for r in rows)
        thresholds[str(threshold)]=dict(tp=tp,tn=tn,fp=fp,fn=fn,accuracy=(tp+tn)/len(rows),
            false_high_fraction_of_reference_low=fp/(fp+tn),precision=tp/(tp+fp),recall=tp/(tp+fn))
    # Resample whole identical-answer groups, retaining dependence inside a group.
    rng=np.random.default_rng(20261108)
    clusters=list(grouped.values());boot=[]
    for _ in range(4000):
        ids=rng.integers(0,len(clusters),size=len(clusters))
        error=np.array([r['signed_error'] for i in ids for r in clusters[int(i)]],dtype=float)
        boot.append([float(abs(error).mean()),float(error.mean()),float(np.mean(abs(error)<=1))])
    boot=np.asarray(boot)
    ci={name:[float(x) for x in np.quantile(boot[:,i],[.025,.975])] for i,name in enumerate(['mae','bias','within1_rate'])}
    confusion=np.zeros((11,11),dtype=int)
    for r in rows:confusion[r['reference_score'],r['model_score']]+=1
    alternatives=check_alternatives()
    metadata=dict(date='2026-11-08',model_name_user_reported='DeepSeek-V4.1-Flash-1017',platform='CodeArts Agent',
        experiment_type='题目与参考答案直接评分基线，未使用本团队设计的校准Agent',prompt=PROMPT,
        prompt_contains_scored_examples=False,prompt_contains_explicit_point_rubric=False,
        requested_scope='一次任务要求批改整份107条答案文件；底层调用与拆批方式未提供',
        temperature=None,number_of_model_runs=1,actual_model_call_count=None,latency_seconds=None,cost=None,
        input_no_score_fields_verified=True,input_matches_current_answers=True,
        truth_snapshot_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        input_sha256=hashlib.sha256((TEST/'04_全部答案_无得分.jsonl').read_bytes()).hexdigest(),
        prediction_attachment_sha256=hashlib.sha256(ATTACHMENT.read_bytes()).hexdigest(),
        reason_over_100_characters=sum(r['model_reason_length']>100 for r in rows),
        unique_text_groups=len(grouped),duplicate_groups=len(duplicate_groups),duplicate_consistent_groups=duplicate_consistent)
    result=dict(metadata=metadata,all=all_stats,by_reference_score=by_score,subsets=subsets,
        by_answer_length=lengths,thresholds=thresholds,cluster_bootstrap_95_ci=ci,
        confusion_rows_reference_columns_model=confusion.tolist(),
        error_counts=dict(sorted(Counter(r['signed_error'] for r in rows).items())),
        reference_score_counts=dict(sorted(Counter(r['reference_score'] for r in rows).items())),
        model_score_counts=dict(sorted(Counter(r['model_score'] for r in rows).items())),
        macro_mae_by_reference_score=float(np.mean([v['mae'] for v in by_score.values()])),
        constant_full_score_mae=float(np.mean([10-r['reference_score'] for r in rows])),
        alternative_algorithm_interpretations=alternatives)
    shutil.copyfile(ATTACHMENT,OUT/'011_模型批改原始结果.json')
    for p in [TEST/'01_题目与参考答案.md',TEST/'04_全部答案_无得分.jsonl']:
        shutil.copyfile(p,snapshot/p.name)
    shutil.copyfile(source,snapshot/'011_当前基准分数快照.jsonl')
    write_json(OUT/'011_实验配置.json',metadata)
    write_json(OUT/'011_统计结果.json',result)
    (OUT/'011_逐条评分对照.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    make_charts(rows,result,confusion)
    make_report(rows,result)
    make_cases(rows)
    assert all_stats['exact_count']+all_stats['over_count']+all_stats['under_count']==107
    assert int(confusion.sum())==107
    assert len(read_jsonl(OUT/'011_逐条评分对照.jsonl'))==107
    print(json.dumps(dict(output=str(OUT),all=all_stats,ci=ci,unique_texts=subsets['unique_texts'],thresholds=thresholds,alternatives=alternatives),ensure_ascii=False))


def make_charts(rows,result,cm):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font=Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams['axes.unicode_minus']=False
    plt.rcParams['font.size']=10
    fig,axes=plt.subplots(2,2,figsize=(13,9),constrained_layout=True)
    a=result['all'];scores=np.arange(11)
    ax=axes[0,0]
    y=np.array([r['reference_score'] for r in rows]);p=np.array([r['model_score'] for r in rows])
    ax.bar(scores-.2,np.bincount(y,minlength=11),width=.4,label='当前基准',color='#465e75')
    ax.bar(scores+.2,np.bincount(p,minlength=11),width=.4,label='模型评分',color='#dc9250')
    ax.set(title='分数分布：模型给出更多高分',xlabel='分数',ylabel='答案数',xticks=scores);ax.legend()
    ax=axes[0,1]
    for ref in range(11):
        for model in range(11):
            n=cm[ref,model]
            if n:
                ax.scatter(ref,model,s=30+30*n,c='#cb7640' if model>ref else '#4e7e99',alpha=.75,edgecolors='white')
                if n>=3:ax.text(ref,model,str(n),ha='center',va='center',fontsize=8)
    ax.plot([0,10],[0,10],'--',color='#65717c',linewidth=1)
    ax.set(title='基准与模型：虚线上方为模型分数较高',xlabel='当前基准分数',ylabel='模型分数',xticks=scores,yticks=scores,xlim=(-.5,10.5),ylim=(-.5,10.5));ax.grid(alpha=.15)
    ax=axes[1,0]
    errors=Counter(r['signed_error'] for r in rows);xs=range(-3,8)
    ax.bar(list(xs),[errors[x] for x in xs],color=['#4e7e99' if x<0 else '#687c88' if x==0 else '#dc9250' for x in xs])
    ax.axvline(0,color='#6b7781',linewidth=1)
    ax.set(title=f'差值分布：平均高出 {a["bias"]:.2f} 分',xlabel='模型分数 − 当前基准分数',ylabel='答案数',xticks=list(xs))
    ax=axes[1,1]
    means=[result['by_reference_score'].get(str(i),{}).get('model_mean',np.nan) for i in scores]
    ax.plot(scores,means,'o-',color='#c1763e',label='该基准分数下的模型平均分')
    ax.plot([0,10],[0,10],'--',color='#65717c',label='完全一致')
    for i in scores:
        m=result['by_reference_score'].get(str(i))
        if m:ax.annotate(f'n={m["n"]}',(i,means[i]),xytext=(0,7),textcoords='offset points',ha='center',fontsize=8)
    ax.set(title='中低分组分歧更大（部分组样本很少）',xlabel='当前基准分数',ylabel='模型平均分',xticks=scores,ylim=(-.3,10.8));ax.legend(fontsize=8)
    fig.suptitle(f'011题直接评分基线｜N=107｜MAE={a["mae"]:.2f}｜±1分一致率={a["within1_rate"]:.1%}',fontsize=15)
    fig.savefig(OUT/'011_评分对比图.png',dpi=160);plt.close(fig)
    fig,ax=plt.subplots(figsize=(9,8),constrained_layout=True)
    im=ax.imshow(cm,cmap='Blues',origin='lower')
    for i in range(11):
        for j in range(11):
            if cm[i,j]:ax.text(j,i,str(cm[i,j]),ha='center',va='center',color='white' if cm[i,j]>cm.max()*.55 else '#203345')
    ax.set(xticks=scores,yticks=scores,xlabel='模型分数',ylabel='当前基准分数',title='011题评分混淆矩阵（单元格为答案数）')
    fig.colorbar(im,ax=ax,label='答案数',shrink=.8)
    fig.savefig(OUT/'011_评分混淆矩阵.png',dpi=160);plt.close(fig)


def make_report(rows,result):
    a=result['all'];ci=result['cluster_bootstrap_95_ci'];t=result['thresholds'];by=result['by_reference_score'];s=result['subsets']
    lines=['# 011题普通大模型直接评分：对比分析报告\n',
        '分析日期：2026年11月8日。模型名称按用户实验记录为 **DeepSeek-V4.1-Flash-1017**，运行于 **CodeArts Agent**；未使用本团队设计的教师校准Agent。\n',
        '## 1. 主要结论\n',
        f'本次107份答案全部成功对齐。相对当前基准，模型平均高出 **{a["bias"]:.2f}分**，平均绝对误差 **{a["mae"]:.2f}分**；完全同分率 **{a["exact_rate"]:.1%}**，误差不超过1分的比例 **{a["within1_rate"]:.1%}**。差异主要集中在中低分答案，模型倾向给出更高分，满分数量由基准的18份增加到46份。\n',
        '**这证明当前评分口径尚未对齐，不能直接解释成模型有77.6%的答案“判错”。** 当前基准107条中只有7条在上一轮得到教师明确复核，其余100条仍是数据集原始人工标签。部分最大分差恰好涉及可行的替代算法，原低分值得进一步复核；同时也发现模型理由与原文矛盾、漏检代码分支错误等可直接检查的问题。\n',
        '![评分对比图](011_评分对比图.png)\n',
        '## 2. 实验设置与数据可信度\n',
        '- 输入：题目、参考答案、满分10分，以及107条无分答案。已逐条核对测试目录的输入与当前数据包一致，字段仅为answer_id、q_id、answer，没有score。',
        '- 用户提供的提示词没有带分样例和显式得分细则，适合作为“参考答案直接评分”的第一条基线。代码或文件读取由CodeArts Agent完成，不能当作已经测量过的无工具裸模型API调用。',
        '- 一次评分结果；是否内部拆批、模型调用次数、温度、耗时、成本及完整代理上下文未提供。本报告不据此评价速度、成本或多次运行稳定性。',
        '- 对齐结果：107个唯一编号，无缺失、重复、额外编号；107条分数均为0—10整数，理由均非空，均未超过100个字符。',
        '- 当前基准：7条teacher_reviewed、100条尚未重新核分的原标签。已复核7条只有2种去空白后的不同文本，不能把它们当成7个独立、丰富的教师标准案例。\n',
        '**实际提示词：**\n','```text',PROMPT,'```\n',
        '## 3. 总体分数一致性\n',
        '| 指标 | 实测 | 含义 |','| --- | ---: | --- |',
        f'| 平均基准分 / 模型分 | {a["reference_mean"]:.2f} / {a["model_mean"]:.2f} | 模型整体偏高 |',
        f'| MAE | {a["mae"]:.3f} | 每份答案平均相差多少分，越低越好 |',
        f'| RMSE | {a["rmse"]:.3f} | 对较大的分差赋予更高权重 |',
        f'| 平均有符号差 | +{a["bias"]:.3f} | 模型分−基准分 |',
        f'| 完全同分 | {a["exact_count"]}/107，{a["exact_rate"]:.1%} | 不是经教师重新裁定的正确率 |',
        f'| 误差≤1分 | {a["within1_count"]}/107，{a["within1_rate"]:.1%} | 容差内一致率 |',
        f'| 误差≤2分 | {a["within2_count"]}/107，{a["within2_rate"]:.1%} | 容差内一致率 |',
        f'| 绝对差≥3分 | {a["abs_ge3_count"]}/107，{a["abs_ge3_count"]/107:.1%} | 本报告探索性定义的较大分差 |',
        f'| 绝对差≥4分 | {a["abs_ge4_count"]}/107，{a["abs_ge4_count"]/107:.1%} | 优先人工检查 |',
        f'| 模型高于 / 低于基准 | {a["over_count"]} / {a["under_count"]}条 | 另外24条相同 |',
        f'| 最大差值 | +{a["max_over"]:.0f} / {a["max_under"]:.0f}分 | 最大正差不代表已证实错给分 |',
        f'| Pearson / Spearman | {a["pearson"]:.3f} / {a["spearman"]:.3f} | 有一定线性及排序关联，但不代表同分 |',
        f'| 二次加权Kappa | {a["quadratic_weighted_kappa"]:.3f} | 0—10共11档，较大分差惩罚更重 |',
        f'| 按基准分档平均的MAE | {result["macro_mae_by_reference_score"]:.3f} | 每个出现的基准分档等权，减少高分样本多的影响 |\n',
        f'按98个去空白同文组做4,000次组级bootstrap，MAE的95%区间约为 **[{ci["mae"][0]:.2f}, {ci["mae"][1]:.2f}]**，平均有符号差约 **[{ci["bias"][0]:.2f}, {ci["bias"][1]:.2f}]**。这只反映该批样本的抽样不确定性，不覆盖标签错误、换题泛化或模型多次运行波动。\n',
        f'作为简单参照，若107份全给10分，MAE为{result["constant_full_score_mae"]:.2f}；当前模型的1.72更低，说明它确实在区分答案，不能仅因一致率低就说完全没有评分能力。该常数参照也不是对完整Agent的效果比较。\n',
        '## 4. 不同基准分档表现\n',
        '| 基准分 | 数量 | 模型平均分 | MAE | 平均差值 |','| ---: | ---: | ---: | ---: | ---: |']
    for grade,m in by.items():lines.append(f'| {grade} | {m["n"]} | {m["model_mean"]:.2f} | {m["mae"]:.2f} | {m["bias"]:+.2f} |')
    lines += ['\n原3分与4分组的模型平均分都为7.4，差异尤其突出；两组都只有5条，不宜把该均值当作稳定的普遍规律。原10分组平均模型分9.72、MAE0.28，模型对高分答案与当前基准更接近。\n',
        '## 5. 分数阈值与满分判断\n',
        '下表用6、9、10分作探索性阈值；6分仅是假设的业务界线，并非教师已经确定的验收标准。“误判”在此仅指相对当前基准的阈值不一致。\n',
        '| 阈值 | 基准未达标→模型达标 | 基准达标→模型未达标 | 阈值一致率 |',
        '| --- | ---: | ---: | ---: |']
    for cut,m in t.items():lines.append(f'| ≥{cut}分 | {m["fp"]} | {m["fn"]} | {m["accuracy"]:.1%} |')
    lines += ['\n模型给46份满分，其中16份基准也为10分、30份基准低于10分；基准18份满分中另有2份被模型打成更低分。因此模型满分判定与基准匹配16/46=34.8%。不能把其余30份全部宣判为错给满分，需结合后面的替代解法和标签复核。\n',
        '若暂按6分界线，基准低于6分的32份中，有22份被模型给到6分以上，占68.8%；这是部署前值得关注的口径差异，但仍需先确认这32份的人工标准可靠。\n',
        '## 6. 已复核样本、重复文本和原学习子集\n',
        '| 子集 | N | MAE | 平均差值 | 完全同分率 |','| --- | ---: | ---: | ---: | ---: |']
    names={'teacher_reviewed':'本轮教师已复核','not_rereviewed':'未重新核分的原标签','unique_texts':'去空白文本去重后','existing_learning_subset':'原10%学习子集','remaining_subset':'其余答案'}
    for key,name in names.items():
        m=s[key];lines.append(f'| {name} | {m["n"]} | {m["mae"]:.2f} | {m["bias"]:+.2f} | {m["exact_rate"]:.1%} |')
    lines += ['\n- 已复核7条均为教师9分、模型10分，表现为明确的1分口径差。它们仅有2种文本，不能据此声称教师独立验证了一整套评分能力。',
        '- 共有5个重复组、涉及14条答案，模型在这5组内均给出一致分数；这是同一批内的一致性，不能证明跨次运行稳定。去重后仍有98种文本，可减轻重复答案权重。',
        '- 此处的“原学习子集”只是历史数据划分标记。当前提示词没有提供带分学习样例，因此这两行不表示本次已经训练/校准过，也不能作训练集与测试集效果解释。全量评分后已分析的这批数据应记为开发评测资料。\n',
        '## 7. 答案长度与输出可核查性\n',
        '| 原答案字符数 | N | MAE | 平均差值 |','| --- | ---: | ---: | ---: |']
    for label,m in result['by_answer_length'].items():lines.append(f'| {label} | {m["n"]} | {m["mae"]:.2f} | {m["bias"]:+.2f} |')
    lines += ['\n字符数不等于token数。各组题解类型和原分构成不同，本表只能描述现象，不能据此认定模型偏好长答案。\n',
        '107条均有理由，便于初步检查；但输出只有总分和短理由，没有逐项配分、学生原文引文、固定规则版本、不确定标记，因此无法直接算逐得分点正确率、引文支持率或风险分流质量。不能把“理由非空”当作理由可靠。\n',
        '## 8. 理由与算法正确性：关键案例\n',
        '| 答案编号 | 当前基准 / 模型 | 初步解释 |','| --- | ---: | --- |']
    indexed={r['answer_id']:r for r in rows}
    for aid,title,_ in MANUAL:
        r=indexed[aid];lines.append(f'| {aid} | {r["reference_score"]} / {r["model_score"]} | {title} |')
    lines += ['\n完整学生原文、模型理由和人工初步分析见[差异案例复核清单](011_差异案例复核清单.md)。',
        '\n为复核020与037/057的替代算法，程序按照文字含义自行重建了两种栈操作方案，各检查1—10步的全部合法入队/出队序列，共各525条，输出与FIFO队列一致。这不是执行学生原代码，也不能替代教师对措辞、空队列保护和效率要求的裁定；它支持“替代方案本身可行”的判断。\n',
        '**当前至少有两种方向的问题同时存在：** 模型会误读明确写出的内容或漏掉分支错误；数据集原标签也可能对正确替代算法给了偏低分。大分差应先进入复核，而不是直接计作模型错误。\n',
        '## 9. 对我们Agent设计与下一轮实验的启示\n',
        '1. **先补可靠评测标准。** 优先请教师盲看037、020、057、058、077、038等案例，不预先展示模型分数；确认替代算法是否接受、是否考查效率、笔误及省略语怎样扣分，再冻结测试标签。不要为了让本项目赢而按模型分差选择有利标准。',
        '2. **从缺失口径提炼可检查规则。** 规则至少区分原参考的双持久栈方案与每次还原到单栈的方案，分别判断判空条件；不要要求所有解法都写“两栈均空”。明确正确文字与错误代码冲突时怎么给分。',
        '3. **真正校准后再比较。** 使用同一底层模型、相同题目和最终测试：比较①当前参考答案直接评分；②同样少量带分样例的直接评分；③规则提炼＋教师确认＋案例检索＋疑难复核的Agent。②不可省略，否则样例带来的提升会被算成Agent架构贡献。',
        '4. **做针对性的代码和证据检查。** 例如S2空、S1仅一个元素时是否能出队；每次出队后是否还原到同一栈；理由里提到“没写”的内容是否真的缺失。对不能确定的替代算法转人工，不把模板差异直接判错。',
        '5. **另外测稳定性和人工负担。** 固定模型版本与参数，重复并打乱答案顺序，比较分数波动；记录教师准备规则、校准、复核、返工的总时间，以及调用数、延迟和成本。本轮没有这些数据，暂不能宣称效率提升。\n',
        '**当前结论：** 普通大模型已具备一定的题意理解、错因定位和替代算法识别能力，但与当前基准存在偏高及配分差异，理由可靠性也有具体缺陷；本轮尚不能证明我们未运行的Agent优于它。项目应验证教师校准与复核是否在可靠标签上改善一致性、证据质量和人工总成本。\n',
        '## 10. 文件与复现\n',
        '- [原始模型结果](011_模型批改原始结果.json)：用户粘贴结果原样保存。',
        '- [逐条评分对照](011_逐条评分对照.jsonl)：107条，含基准、原数据分、教师复核状态、模型分、差值、原文与理由。',
        '- [统计结果](011_统计结果.json)、[实验配置与提示词](011_实验配置.json)。',
        '- [混淆矩阵](011_评分混淆矩阵.png)。输入、题目与基准快照保存在`评测输入快照`；脚本为测试目录下`分析011评分结果.py`。',
        '- 后续独立评分使用干净目录，只交给待测模型题目、参考答案和无分输入，避免其读取本分析目录中的基准分及结果。\n']
    (OUT/'011_普通模型评分对比报告.md').write_text('\n'.join(lines),encoding='utf-8')


def make_cases(rows):
    indexed={r['answer_id']:r for r in rows}
    lines=['# 011题差异案例复核清单\n',
        '本清单不修改任何基准分数。优先讨论标签可靠性、替代算法、代码错误及模型理由是否受原文支持；并非把全部分差归错于模型。\n',
        '## 一、优先人工检查的案例\n']
    for aid,title,note in MANUAL:
        r=indexed[aid]
        status='本轮教师已复核' if r['reference_status']=='teacher_reviewed' else '数据集原标签，尚未重新核分'
        lines += [f'### {aid}｜{title}\n',
            f'当前基准：{r["reference_score"]}；模型：{r["model_score"]}；差值：{r["signed_error"]:+d}。基准状态：{status}。\n',
            '**学生原文：**\n','```text',r['answer'].rstrip(),'```\n',
            '**模型理由：** '+r['model_reason']+'\n','**初步复核：** '+note+'\n',
            '**教师裁定：** 确认分数______；可接受解法与理由______；是否需要补充统一规则______。\n']
    lines += ['## 二、全部绝对差≥3分的记录\n',
        '| 答案编号 | 当前基准 | 模型 | 差值 | 基准状态 |', '| --- | ---: | ---: | ---: | --- |']
    for r in sorted((r for r in rows if r['absolute_error']>=3),key=lambda r:(-r['absolute_error'],r['answer_id'])):
        lines.append(f'| {r["answer_id"]} | {r["reference_score"]} | {r["model_score"]} | {r["signed_error"]:+d} | {r["reference_status"]} |')
    lines += ['\n这些27条的分差不等于已经证实的27次模型错判。其余样本与完整理由见逐条对照JSONL。\n',
        '## 三、教师已经确认的7条锚点\n','| 答案编号 | 教师分 | 模型分 | 差值 |','| --- | ---: | ---: | ---: |']
    for r in rows:
        if r['reference_status']=='teacher_reviewed':lines.append(f'| {r["answer_id"]} | {r["reference_score"]} | {r["model_score"]} | {r["signed_error"]:+d} |')
    lines += ['\n7条全部是教师9、模型10，但只代表两种不同文本。可用来讨论“省略分支但含义可推断”扣1分的口径，不宜作为7个独立证据。\n']
    (OUT/'011_差异案例复核清单.md').write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':main()
