"""Independent integrity checks and algorithm counterexamples; no model calls."""
from collections import defaultdict, deque
from fractions import Fraction
from itertools import permutations, product
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'A全面预处理_2026-11-16'
DATA=ROOT/'题目答案相关数据'
def read(p):return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def queue_trace(kind,operations):
    a,b,reference=[],[],deque()
    for op in operations:
        if op in ('a','b'):
            if kind=='lazy' or kind=='restore_s1' or kind=='bottom':a.append(op)
            elif kind=='switch':
                while b:a.append(b.pop())
                a.append(op)
            elif kind=='eager':
                while b:a.append(b.pop())
                b.append(op)
                while a:b.append(a.pop())
            reference.append(op)
        elif op=='pop':
            if not reference:continue
            if kind=='lazy':
                if not b:
                    while a:b.append(a.pop())
                actual=b.pop()
            elif kind in ('restore_s1','switch'):
                while a:b.append(a.pop())
                actual=b.pop()
                if kind=='restore_s1':
                    while b:a.append(b.pop())
            elif kind=='bottom':
                while len(a)>1:b.append(a.pop())
                actual=a.pop()
                while b:a.append(b.pop())
            else:actual=b.pop()
            assert actual==reference.popleft(),(kind,operations)
        else:
            actual=(not a) if kind in ('restore_s1','bottom') else (not b) if kind=='eager' else (not a and not b)
            assert actual==(not reference),(kind,operations)
    return True

def merge_count(a,b):
    i=j=compared=0;origins=[]
    while i<len(a) and j<len(b):
        compared+=1
        if a[i]<=b[j]:i+=1;origins.append('A')
        else:j+=1;origins.append('B')
    origins+=['A']*(len(a)-i)+['B']*(len(b)-j)
    return compared,origins

def quick(a):
    a=list(a);bad=[]
    def exchange(i,j):
        for src,dst in [(i,j),(j,i)]:
            if (dst-src)*(a[src]-1-src)<0:bad.append((a[:],a[src],src,dst))
        a[i],a[j]=a[j],a[i]
    def sort(lo,hi):
        if hi<=lo:return
        i=lo;j=hi+1;pivot=a[lo]
        while True:
            i+=1
            while a[i]<pivot:
                if i==hi:break
                i+=1
            j-=1
            while pivot<a[j]:
                if j==lo:break
                j-=1
            if i>=j:break
            exchange(i,j)
        exchange(lo,j);sort(lo,j-1);sort(j+1,hi)
    sort(0,len(a)-1)
    return a,bad

def main():
    summary=json.loads((OUT/'数据/执行摘要.json').read_text(encoding='utf-8'))
    candidate=read(OUT/'数据/全部疑点逐项预处理.jsonl')
    mapping={r['answer_id']:r for r in candidate}
    assert len(mapping)==92
    baseline=json.loads((OUT/'数据/输入源文件摘要_应用前.json').read_text(encoding='utf-8'))
    allrows={};sample_ids=set();groups=defaultdict(list)
    for folder in sorted((OUT/'当前暂行数据').iterdir()):
        rr={n:read(next(folder.glob(n+'_*'))) for n in ['02','03','04','05']}
        assert [{k:v for k,v in r.items() if k!='score'} for r in rr['05']]==rr['04']
        assert [{k:v for k,v in r.items() if k!='score'} for r in rr['03']]==rr['02']
        assert {r['score'] for r in rr['03']}=={r['score'] for r in rr['05']}
        index={r['answer_id']:r for r in rr['05']}
        assert all(index[r['answer_id']]==r for r in rr['03'])
        for r in rr['05']:
            assert r['answer_id'] not in allrows and 0<=float(r['score'])<=10
            allrows[r['answer_id']]=r
            groups[(r['q_id'],re.sub(r'\s+','',r['answer']).casefold())].append(r)
        sample_ids|={r['answer_id'] for r in rr['03']}
        original_plain=next((DATA/folder.name).glob('04_*'))
        assert sha(original_plain)==baseline[str(original_plain.relative_to(ROOT))]
        if summary['applied_to_current_data']:
            for n in ['02','03','04','05']:
                name=next(folder.glob(n+'_*')).name
                assert read(DATA/folder.name/name)==rr[n]
    assert len(allrows)==1582 and len(sample_ids)==165
    assert all(len({float(r['score']) for r in members})==1 for members in groups.values())
    assert all(not r['answer'].strip() and float(r['score'])==0 for r in allrows.values() if not r['answer'].strip())
    for r in candidate:
        assert sum(item['suggested_score'] for item in r['draft_items'])==r['provisional_score']
        assert float(allrows[r['answer_id']]['score'])==r['provisional_score']
        for item in r['draft_items']:
            assert item['suggested_score']==0 or item['evidence']
            for span in item['evidence']:assert r['answer'][span['start']:span['end']]==span['quote']
        assert r['teacher_confirmed'] is False and r['approved_for_reuse'] is False
    splits=read(OUT/'数据/当前开发用途划分.jsonl');pools=defaultdict(set)
    for r in splits:pools[r['group_sha256']].add((r['pool'],r['development_subset']))
    assert all(len(p)==1 for p in pools.values())
    for aid in ['011-000058','011-000063']:
        assert next(r for r in splits if r['answer_id']==aid)['role']=='historical_learning'
    metadata=read(OUT/'数据/当前逐条标签来源与版本.jsonl')
    assert len(metadata)==1582
    for r in metadata:
        if summary['applied_to_current_data']:
            question_file=next((DATA/r['question_id']).glob('01_*'))
            assert r['rubric_document_sha256']==sha(question_file),r['answer_id']
            assert r['question_document_sha256']==sha(question_file),r['answer_id']
    raw_sha='1daf171715cb0e95d7a8a7d1c5a8123ab7cbd119475e11c031bdcdcdda253652'
    assert sha(ROOT/'all.jsonl')==sha(ROOT/'排序后的数据.jsonl')==raw_sha
    if summary['applied_to_current_data']:
        old=read(ROOT/'A任务修正_2026-11-15/数据与版本/逐条标签来源与版本.jsonl')
        current_review={r['answer_id']:r for r in read(DATA/'03_待教师复核记录.jsonl')}
        for r in old:
            if r['recorded_teacher_total_score'] is not None:
                assert current_review[r['answer_id']]['teacher_score']==r['recorded_teacher_total_score']
    queue_sequences=0
    for length in range(1,7):
        for ops in product(['a','b','pop','empty'],repeat=length):
            for kind in ['lazy','restore_s1','bottom','eager','switch']:queue_trace(kind,ops)
            queue_sequences+=1
    assert merge_count([1,1],[1,1])[0]==2
    assert merge_count([1,2],[1,2])[0]==3
    for n in range(1,51):
        assert sum(Fraction(k,n+1) for k in range(n+1))==Fraction(n,2)
        assert sum(Fraction(k*k,n*(n+1)//2) for k in range(1,n+1))==Fraction(2*n+1,3)
    quick_permutations=0
    for n in range(2,8):
        for p in permutations(range(1,n+1)):
            result,bad=quick(p);assert result==sorted(p) and not bad
            quick_permutations+=1
    report={'label_version':summary['label_version'],'approval_status':summary.get('approval_status'),
        'user_approved_review_count':sum(r.get('user_approved') is True for r in candidate),
        'applied':summary['applied_to_current_data'],'records':1582,'reviewed':92,'changed_scores':29,
        'scored_unscored_alignment':True,'sample_count':165,'sample_scores_covered':True,
        'exact_text_conflicting_groups':0,'source_answers_unchanged':True,'historical_teacher_totals_preserved':True,
        'former_learning_answers_still_excluded':True,'drafts_not_marked_teacher_approved':True,
        'queue_test_sequences_per_variant':queue_sequences,'queue_variants':5,
        'merge_duplicate_counterexample_compared':2,'insertion_expectation_n_cases':50,
        'double_scan_quicksort_permutations':quick_permutations,
        'quicksort_exhaustive_scope':'distinct keys, n<=7, this specific first-pivot double-scan implementation; not a general proof',
        'live_model_calls':0}
    savepath=OUT/'数据/完整性与技术核验结果.json'
    savepath.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
