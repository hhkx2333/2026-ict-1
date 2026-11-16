"""Apply explicitly attributed AI provisional revisions for teacher review, never teacher approval."""
import argparse
from collections import Counter, defaultdict
import difflib
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'题目答案相关数据'
OUT=ROOT/'A全面预处理_2026-11-16'
VERSION='2026-11-16-ai-provisional-v1'
PREVIOUS='2026-11-10-user-approved-v1'
BROOT=ROOT/'review-b-2026-11-15/unpacked/2026-ict-1'

# A concrete provisional policy is supplied for every question, not a blank checklist.
# Each tuple is (maximum, allowed scores, criterion title, full partial-credit policy).
POLICIES={
'001':[(2,[0,1,2],'存储位置','连续存储和结点不必物理连续各1分。'),(2,[0,1,2],'访问方式','顺序表随机访问与链表通常沿链访问各1分。'),(4,[0,2,4],'空间分配','静态顺序表预分配与链表动态申请各2分；承认动态数组例外。'),(2,[0,1,2],'链接开销','顺序表不需逐结点指针、链表需链接空间各1分。')],
'002':[(4,[0,2,4],'适用增删情景','明确中间增删少或尾部增删4分；混合正确尾部和错误头部情景2分。'),(4,[0,1,3,4],'访问需求','下标随机访问4分；只说查询/读取多3分；只说速度快1分。'),(2,[0,1,2],'选择依据','明确定位/移动或连续容量依据2分；仅有合理情景对照或紧凑性1分。')],
'003':[(3,[0,3],'结构选择','选择链式存储3分。'),(3,[0,1,2,3],'改链机制','明确调整指针3分；只说无需整体搬移2分；只说方便1分。'),(4,[0,1,2,3,4],'比较与正确性','链表不搬移其他元素、顺序表通常移动各2分；附带无限空间等错误绝对结论时本项最多3分，不重复扣。')],
'004':[(5,[0,2,3,5],'首元操作统一','首部增删统一2分，首元有前驱或免特殊分支3分。'),(5,[0,2,3,5],'空表处理统一','空/非空均保留头结点3分，统一边界或head.next判空2分；近义句不重复算。')],
'005':[(3,[0,1,2,3],'普通单链表','默认删除原物理结点：一般不能1分，找不到前驱2分。明确仅删除逻辑元素、复制后继且p非尾的O(1)替代说明也可得本项满分；不得声称尾结点也可如此删除。'),(3,[0,1,2,3],'双链表','可以、改前后链接、O(1)各1分。'),(4,[0,1,2,3,4],'循环单链表','可以1分，沿环找前驱并改链2分，O(n)1分；单结点环需由调用者处理外部入口。')],
'006':[(3,[0,3],'否定绝对说法','明确错误或有效反例3分。'),(3,[0,1,3],'栈限制','解释只从栈顶删除3分；仅LIFO术语1分；完整单元素反例可隐含。'),(4,[0,2,4],'合法删除','逐个移走上方元素后删底，或单元素栈反例4分；方向正确不完整2分。')],
'007':[(3,[0,3],'判断','不一定按值有序3分，正确解释中的否定可隐含。'),(4,[0,2,4],'顺序存储含义','数组/顺序存储实现4分；只说位置固定2分；结合题干的存储结构有序可按顺序存储理解。'),(3,[0,2,3],'值序区分','值可有序或无序3分；绝对称元素无序但区分了存储和值2分。')],
'008':[(3,[0,1,3],'时序与判断','按字面全部先入后出则逆序；明确允许交错则不一定；在声明的前提下自洽3分，只有无前提判断1分。'),(3,[0,1,3],'LIFO解释','结合操作时序解释3分，仅术语1分。'),(4,[0,2,4],'有效过程','完整可执行过程或全部先入的充分推理4分；仅合法输出序列2分；132须附交错操作，不能据其否定全部先入前提。')],
'009':[(3,[0,1,2,3],'独立顺序栈','简单、容量不能互借、溢出/浪费各1分。'),(4,[0,1,2,3,4],'共享顺序空间','容量调剂2分、维护边界/空闲管理2分；每半仅含糊各1分；不要求所有实现都搬移元素。'),(3,[0,1.5,3],'独立链栈','动态申请与指针/分配开销各1.5分，仍受内存限制。')],
'010':[(2,[0,1,2],'FIFO及入栈顺序','FIFO与进入栈的序列不变各1分。'),(3,[0,1,2,3],'LIFO与首个输出','LIFO1分，a1=3n推出全部已入且尚未有元素输出2分。'),(5,[0,2,4,5],'结果','3(n-i+1)及范围或完整等价逆序序列5分；实质逆序正确但边界缺失4分；只说逆序2分。')],
'011':[(2,[0,1,2],'入队','完整压入正确持久结构2分；变量笔误、条件或关键步骤不完整1分；仅标题0分。'),(2,[0,1,2],'已有输出元素时出队','非空条件与正确取队首各1分。只写搬移后弹出不重复充当本分支。'),(3,[0,1,2,3],'需要反转时出队','正确触发条件、全部逐个搬移、随后取队首各1分；证据必须来自相应有效流程，不借入队的满栈搬移。'),(2,[0,1,2],'判空','与实现不变量一致的充要判空2分；只写正确的非空充分条件或不完整判断1分；错误充要判空0分。'),(1,[0,1],'FIFO依据','正确概念解释或完整正确流程隐含顺序保持1分；概念可独立于错误代码计分，含糊/不完整流程不自动给。')],
'013':[(3,[0,1,3],'最少情形','两表值域分离可直接相接3分；方向不完整1分；等号边界须结合平局规则。'),(2,[0,1,2],'最少次数解释','一表先耗尽并复制剩余2分；只复述n次1分。'),(3,[0,1,2,3],'最多情形','最后两个输出元素来源不同3分，严格交错为充分例；只说相同序列但未限制重复值2分；含糊交错1分。'),(2,[0,1,2],'最多次数解释','持续比较到只剩最后一个元素2分；条件不充分但明确轮流取与2n-1的推理1分。')],
'014':[(5,[0,5],'均匀插入期望','n+1个位置、移动0..n，结果n/2得5分；错误或未答0分。'),(5,[0,5],'非均匀期望','用移动量加权概率得(2n+1)/3为5分；仅求概率和不给分。只用0/5/10总分，不改学生括号。')],
'015':[(3,[0,1,2,3],'冒泡有效过程','具体合法完整趟次3分；有效局部交换2分；仅合理构造1分；声称完整趟次却结果错误0分。'),(3,[0,1,2,3],'反向移动依据','关键字、最终位置/方向、实际相反移动各1分，可由明确例子隐含。'),(2,[0,2],'快排判断','以明确的具体划分实现作判断；教材首枢轴双向划分的没有或Lomuto的有效反例都接受，不无条件推广。'),(2,[0,1,2],'快排依据','正确实现原理或可执行反例2分，仅部分依据1分；只说二分0分。')],
'019':[(3,[0,1,3],'高度与链形','本轮统一根为第1层、n>=1，n结点高度n推出单链3分；局部方向1分。'),(4,[0,1,2,3,4],'独立选择','n-1条连接2分、每处左右两种2分；仅满树末层类比且变量未定义1分。'),(3,[0,3],'计数','无歧义2^(n-1)3分。题干只问个数，直接正确结果可走总分10的等价答案路线，不要求凭空补推导。')],
}

NOTES={
'005':'明确物理结点删除与逻辑元素删除；复制后继只适用于非尾结点。',
'008':'将原参考的无前提“错误、132”修为两种明确时序；默认按全部先入后出解释，允许有声明的交错解释。',
'011':'正确还原S1、提前整理到S2等替代算法按整体不变量映射出队5分；题目未要求复杂度，不额外扣效率分。一次出队解释为移除一个队首。代码注明条件的伪代码段按所注明情形评价；明确写反条件不能由正确概述覆盖。普通清晰的输入笔误不擅自改学生文本。',
'013':'相同序列不是无条件最多的充分条件；例如A=B=[1,1]、相等时优先A只比较2次而非3次。',
'015':'升序、互异关键字，反向指本次移动方向与该元素相对最终位置的正确方向相反。不同划分实现分开讨论。',
'019':'明确根高按层数计且左右有序。按边数计会得到0的另一个问题；当前采用层数口径，持边数口径的答案进入高优先级约定复核，不说其数学推理本身错误。',
}

# Explicit judgments are made per source answer group, not inferred from target totals.
# (representative ID, per-criterion scores, reasoning). Whitespace duplicates inherit.
DECISIONS=[
('001-000083',[0,0,4,0],'只完整比较预分配和动态分配；未独立说明连续性、访问方式和指针开销。'),
('002-000058',[2,3,1],'尾部和中间增删比较有部分正确性，头部增删用顺序表不正确；查询未明确随机访问，情景对照给部分依据分。'),
('003-000061',[3,3,3],'选链表、改指针和移动比较正确，但链表要多大有多大、不会不够是错误绝对说法，相关比较项少1分。'),
('007-000014',[3,4,3],'明确顺序存储实现且元素不一定有序，三点齐全。'),
('007-000102',[3,4,2],'判断和存储/元素区分正确；元素无序是过强表述，允许偶然有序，本项保留2分。'),
('010-000076',[2,3,5],'FIFO、LIFO、首输出条件及完整逆序序列均正确，不强制改写成通项。'),
('011-000005',[2,1,3,2,1],'有完整倒栈、倒栈前判空、双栈判空及FIFO解释；未独立明确S2非空直接出队的条件，非空分支仅操作1分。'),
('011-000020',[2,2,3,2,1],'移开栈底上方元素、弹出最早元素并还原S1，合法替代算法；单持久栈判空正确。'),
('011-000037',[2,2,3,2,1],'每次入队后S2存队列且队首在顶；按每次出队一个解释，临时S1不需参与操作间隙判空。'),
('011-000046',[1,0,1,2,0],'入队方向可辨但未说明全部搬移；出队无正确完整条件和全部转移，仅给所述分支弹出1分及双栈判空2分。'),
('011-000053',[2,2,3,2,1],'分别说明两种非空情形及S2空时全部倒栈，优先消耗S2、双栈判空，流程隐含FIFO；据此暂改10分。'),
('011-000057',[2,2,3,2,1],'新元素置于S2底、旧元素还原到其上，队首在S2顶，合法替代实现。'),
('011-000064',[2,1,3,2,1],'与005含义一致：倒栈前判断S2空，非空直接出队条件未独立展开；其余正确。'),
('011-000069',[1,2,0,2,1],'变量S笔误保留入队方向1；S2非空出队2；出队没有S2空时倒栈；正确FIFO概念独立1，双栈判空2。'),
('011-000083',[2,1,3,0,1],'接受搬移前判S2空和完整倒栈的上下文含义；非空分支条件不补；只判S1空错误；正确局部过程给FIFO1。'),
('011-000084',[0,0,0,0,0],'空白答案，没有任何得分依据。'),
('011-000085',[2,1,0,2,0],'只写S1入栈、S2出栈和双栈判空；未写非空条件、倒栈流程或FIFO解释。'),
('011-000102',[1,2,0,2,0],'入队条件不完整；S2非空出队和双栈判空正确；入队满栈搬移不能补作缺失的出队倒栈。'),
('013-000002',[3,2,2,1],'最少情形完整；相同序列未排除重复值，最多条件不足，但轮流取及2n-1解释保留部分分。'),
('013-000005',[0,0,3,2],'完整正确回答严格交错的最多比较情形；没有回答最少情形。'),
('013-000052',[0,0,0,0],'空白答案。'),
('014-000032',[0,0],'空白答案。'),
('014-000038',[5,5],'当前文本已经按历史教师记录修正括号，两问结果均正确；本轮不再改原文。'),
('014-000049',[0,5],'第一问写2/n错误，第二问正确。'),
('014-000054',[5,0],'第一问正确，第二问仅求概率和，缺少移动量加权。'),
('014-000083',[5,5],'两问正确，使用历史已修正的括号表达。'),
('014-000086',[5,5],'两问的明确正确结果已足够，接受分号和换行差异。'),
('015-000020',[1,0,2,0],'有构造但第二个最小值向前移动是接近最终位置，未举出反向；仅快排判断，无原理。'),
('015-000043',[0,3,2,0],'第一趟结果不合法，本项0；关键字5在首次交换时确实向左远离最终应在的位置，保留反向观察3；快排只有判断2。'),
('015-000070',[0,0,0,0],'空白答案。'),
('019-000011',[0,0,0],'一般公式2*(n-1)错误，仅n=1特例不替代一般计数或过程。'),
('019-000012',[0,0,0],'2^n-1不是2^(n-1)，无过程不能推定只是笔误。'),
('019-000063',[3,1,0],'每层一结点正确；满树最后层类比有局部思路，但i未定义，没有完整左右独立选择和正确公式。'),
('019-000077',[3,4,0],'链形、n-1连接、左右选择正确；最终公式缺少指数括号，结果项0。'),
('019-000087',[0,0,0],'在本轮统一的层数高度约定下不回答目标问题；其边数约定的推理自洽，必须突出给教师核对约定。'),
('011-000002',[2,2,3,2,1],'全部反转后弹出所需队首，再把剩余还原到S1，替代实现正确；按一次操作取一个理解。'),
('011-000004',[2,2,3,2,1],'入队前先还原S2到S1，出队时若S1有元素则反转，否则直接从S2取；按集合式搬移理解可维持不变量。'),
('011-000007',[2,1,3,2,1],'非空分支混合了先弹S1再排S2，不能完整认定该分支取一个队首；其空输出栈的完整反转与判空有依据。'),
('011-000008',[2,1,1,1,0],'入队2，S2出栈操作1，倒栈后弹出1；未说明全部和触发条件；S1有元素则非空只是正确充分条件1。'),
('011-000013',[1,2,1,1,0],'满栈转移后没写新元素入栈；非空出队正确，空S2触发1但未写全部转移和最后弹出；判空结论未写完1。'),
('011-000014',[1,0,2,0,0],'双栈概述仅保留入队方向1、空S2前提和转移后弹出2；具体三步改成循环队列，不能混借rear/front判空。'),
('011-000015',[2,2,3,2,1],'实际代码每次出队都完整还原到S1并正确判空，暂给10；文字中延迟还原的可选优化没有实现，不能扩展该优化后仍只查S1。'),
('011-000017',[2,0,2,2,1],'缺独立非空S2分支和仅空才搬移条件；全部倒栈及随后弹出2，双栈判空2，正确FIFO解释1。'),
('011-000019',[1,0,3,2,1],'有恢复旧元素但非空S2的入队分支漏明确压入新元素；S1非空时反转出队正确，但S1空S2非空的直接出队没写。'),
('011-000024',[2,2,3,2,1],'每次入队先还原S2再压入新值，操作间隙不会同时残留两栈；出队两种情形完整，合法替代流程。'),
('011-000025',[1,2,3,2,1],'移开n-1个后弹出栈底与后续从S2出队正确；入队将S2还原S1未明确全部，暂保留方向1并标注歧义。'),
('011-000038',[2,0,2,2,1],'全部倒栈及弹出清楚，但缺少S2非空直接弹出和仅S2空才搬移；整体反转支持概念分。'),
('011-000045',[1,2,3,1,1],'入队文字混乱仅方向1；优先S2再倒S1的出队及FIFO有依据；判空仅给非空充分条件1。'),
('011-000050',[1,1,0,2,1],'每次新入元素立即移到S2会把新值放到旧队首上，不能认定完整入队；保留方向、直接弹出、判空和正确概述。'),
('011-000052',[2,2,0,2,1],'具体DeQueue把S1非空写成报空，搬移分支只剩空S1时可达，不能给出队搬移分；入队、S2非空出队、判空与概念分别保留。'),
('011-000058',[2,2,2,2,0],'入队和S2非空分支正确；S2空的条件和随后弹出正确，未说明全部逐个搬移；不凭含糊过程补FIFO分。'),
('011-000059',[2,2,1,2,0],'空S2条件正确，但写成S1全部压回S1且漏最后弹出；不自动修正栈名后给搬移分。'),
('011-000062',[1,0,1,0,1],'双栈概述有局部方向和反转概念，具体操作却为循环队列；仅保留方向1、概述转移后弹出1、概念1。'),
('011-000063',[2,0,2,2,1],'批量全部反转与随后弹出有依据，缺S2非空处理和触发条件；判空及隐含FIFO保留。'),
('011-000065',[1,2,2,2,0],'S1满直接压S2破坏已有队首顺序，入队1；非空分支2；空S2条件与随后输出2，没明确全部搬移。'),
('011-000068',[2,2,3,2,1],'各场景以注释明确条件，题目要求简述，可作分场景伪代码而非顺序执行片段；完整反转、弹出、判空和概念均有依据。'),
('011-000070',[2,2,3,2,1],'逐个移走除最早元素外的数据、弹出最早元素再还原，合法替代方案。'),
('011-000073',[1,2,0,2,1],'入队代码pop未调用且混用指针语法，保留方向；实际出队只处理S1空而S2非空，未实现概述的反转；判空和正确概念保留。'),
('011-000077',[2,2,0,2,1],'函数名称可辨认真实操作，不按写反小标题扣两次；具体出队的S1条件写反，按052同类处理。'),
('011-000081',[2,2,3,0,1],'反转、弹出并还原是正确替代出队，可隐含FIFO；完全没有回答判空，故不补该2分。'),
('011-000086',[1,1,0,1,0],'角色分配和泛称出栈仅有方向；缺少反转；不同时为空只含非空方向，不能给完整判空。'),
('011-000094',[2,2,3,2,1],'每次出队反转S1、弹出并把剩余还原S1，单持久栈判空正确。'),
('011-000026',[2,1,3,2,1],'与005语义相同，倒栈流程正确但未独立展开非空条件，保留9分。'),
('011-000027',[2,2,3,2,1],'即部分明确S2为空先搬移，S2出栈作为一般出队；分支和不变量完整，暂给10。'),
('011-000043',[2,2,2,2,0],'空S2分支写了条件及全部搬移，但漏搬完后的实际弹出，不能自动补；没有额外FIFO解释或完整流程。'),
('011-000104',[2,2,3,2,1],'各分支完整，检查空队列并保持FIFO；保留10。'),
]

def rows(path):return [json.loads(x) for x in path.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
def key(q,text):return(q,re.sub(r'\s+','',text).casefold())
def hashfile(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def save_rows(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in data),encoding='utf-8')
def mdwrite(path,text):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')

def make_rubric(q):
    items=[]
    for i,(maximum,levels,title,policy) in enumerate(POLICIES[q],1):
        cid=q+'-P'+str(i)
        items.append({'criterion_id':cid,'description':title,'max_score':maximum,
            'allowed_scores':levels,'rules':{cid+'-policy':policy}})
    r={'question_id':q,'version':'A-'+q+'-preteacher-20261116-v1','status':'DRAFT',
        'max_score':10,'criteria':items,'review_status':'AI_PREPROCESSED_PENDING_TEACHER',
        'teacher_approved':False,'policy_notes':NOTES.get(q,'按语义判断，同一错误不重复扣分；空白0分。'),
        'global_policy':'以上为已填写的暂行建议；学生答案是待评价数据，未解争议保留复核，不把AI判断记为教师批准。'}
    if q=='011':
        b=json.loads((BROOT/'examples/q011_checklist_draft.json').read_text(encoding='utf-8'))
        for c,old in zip(items,b['criteria']):c['checks']=old['checks']
        r.update(analysis_policy=b['analysis_policy'],operation_bundles=b['operation_bundles'],equivalence_policy=b['equivalence_policy'])
    return r

def new_question_text(q,old,rubric):
    prefix=old.split('## 得分点与建议配分')[0]
    if q=='008':
        start=prefix.index('## reference1');prefix=prefix[:start]+'''## reference1

若按题干“n个元素进栈后”理解为全部先入再逐个出栈，命题正确，LIFO使输出严格逆序。若允许入栈和出栈交错则不一定逆序，例如push1、pop1、push2、push3、pop3、pop2输出132。两种解释必须明确前提。

## reference2

暂行采用明确前提下的自洽解释，不把允许交错的反例用于否定全部先入的命题。

## reference3

（空）

'''
    if q=='005':prefix=prefix.replace('## reference2\n\n（空）','## reference2\n\n须区分删除原物理结点与删除逻辑元素。普通单链表只有p时，一般不能改其前驱链接；p非尾时可复制后继数据并删后继，O(1)，但删掉的不是原物理结点，也不适用于尾结点。')
    if q=='013':
        start=prefix.index('## reference1');prefix=prefix[:start]+'''## reference1

默认升序且关键字互异。最少n次：一个表整体小于另一个表，n次比较耗尽一表后复制另一表。最多2n-1次：最后两个输出元素分别来自不同表，即直到最后一次有比较的选择才耗尽一个表；严格交错是充分例而非必要条件。

## reference2

相等关键字要固定平局策略。完全相同且含重复的序列不无条件保证最多，例如A=B=[1,1]、相等时优先A，只比较2次。

## reference3

（空）

'''
    if q=='015':prefix=prefix.replace('## reference3\n\n（空）','## reference3\n\n另一实现反例：Lomuto以末元素为枢轴，对[1,6,4,2,3,5]升序划分时，4由下标2移到1，但最终下标为3，发生反向移动。因此不能把教材双向划分结论推广到所有快排实现。')
    if q=='019':prefix=prefix.replace('## reference1','## 高度约定（本轮暂行）\n\n根为第1层，n>=1，左右孩子有别。若另按边数计高度，n结点高度至多n-1，得到0的是不同约定的问题；原题未写约定，需教师审核本轮统一口径。\n\n## reference1',1)
    table=['## 得分点与建议配分\n\n',f'当前暂行规则版本：`{rubric["version"]}`；标签集：`{VERSION}`。以下已由AI具体拟定，待教师集中审核，不是空白待办，也不是教师发布版。\n\n',
           '| 编号 | 上限 | 合法档位 | 具体判分办法 |\n| --- | ---: | --- | --- |\n']
    for c in rubric['criteria']:table.append(f'| {c["criterion_id"]} | {c["max_score"]} | {c["allowed_scores"]} | {next(iter(c["rules"].values()))} |\n')
    table+=['\n'+rubric['policy_notes']+'\n','\n本题原文与学生答案不改；数学等价表达接受。未解决的意义冲突保留人工复核；本稿用于让教师核对具体处理，不能填成教师已批准。\n']
    return prefix+''.join(table)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    OUT.mkdir(exist_ok=True)
    summary_path=OUT/'数据/执行摘要.json'
    if summary_path.exists() and json.loads(summary_path.read_text(encoding='utf-8')).get('applied_to_current_data'):
        print('本版已经应用；不重写历史分数或教师审阅材料。需要新一轮修订时请创建新版本。')
        return
    active={};qpaths={};oldmd={};datafiles={}
    for folder in sorted(p for p in DATA.iterdir() if p.is_dir() and p.name.isdigit()):
        q=folder.name;qpaths[q]=next(folder.glob('01_*'));oldmd[q]=qpaths[q].read_text(encoding='utf-8')
        for n in ('02','03','04','05'):
            p=next(folder.glob(n+'_*'));datafiles[(q,n)]=(p,rows(p))
        for row in datafiles[(q,'05')][1]:active[row['answer_id']]=row
    before_hash={str(p.relative_to(ROOT)):hashfile(p) for p,_ in datafiles.values()}
    previous_review=rows(DATA/'03_待教师复核记录.jsonl')
    targets={r['answer_id'] for r in previous_review}
    targets|={'011-'+str(n).zfill(6) for n in [26,27,43,46,53,69,83,85,102,104]}
    old_predictions={}
    for folder in ['正常大模型测试改卷能力(无得分点','正常大模型测试改卷能力(有得分点']:
        rr=rows(ROOT/folder/'011_评分对比分析/011_逐条评分对照.jsonl');old_predictions[folder]=rr
        targets|={r['answer_id'] for r in rr if r['absolute_error']>=3}
    groups=defaultdict(list)
    for row in active.values():groups[key(row['q_id'],row['answer'])].append(row)
    selected={key(active[aid]['q_id'],active[aid]['answer']) for aid in targets}
    targets={r['answer_id'] for group in selected for r in groups[group]}
    decision_by_key={}
    for aid,scores,reason in DECISIONS:
        row=active[aid];k=key(row['q_id'],row['answer'])
        assert k not in decision_by_key,aid
        decision_by_key[k]=(scores,reason,aid)
    assert set(decision_by_key)==selected, ('Missing judgments',selected-set(decision_by_key),'Extra judgments',set(decision_by_key)-selected)
    rubrics={q:make_rubric(q) for q in qpaths}
    from importlib.util import spec_from_file_location
    import sys
    sys.path.insert(0,str(BROOT))
    from backend.grading.core import validate_input,validate_output
    from backend.grading.checklist import validate_checklist
    judgments=[];changes=[]
    for aid in sorted(targets):
        source=active[aid];q=source['q_id'];scores,reason,rep=decision_by_key[key(q,source['answer'])]
        rubric=rubrics[q];assert len(scores)==len(rubric['criteria'])
        text=source['answer'];start=0;end=len(text)
        while start<end and text[start].isspace():start+=1
        while end>start and text[end-1].isspace():end-=1
        evidence=[] if start==end else [{'quote':text[start:end],'start':start,'end':end}]
        items=[]
        for score,c in zip(scores,rubric['criteria']):
            assert score in c['allowed_scores'],(aid,c['criterion_id'],score)
            items.append({'criterion_id':c['criterion_id'],'max_score':c['max_score'],
                'suggested_score':score,'evidence':evidence if score>0 else [],
                'evidence_scope':'whole_answer_context' if score>0 else 'omission_or_error',
                'rule_id':next(iter(c['rules'])),
                'reason':f'{c["description"]}暂计{score}/{c["max_score"]}。判分办法：{next(iter(c["rules"].values()))} 本份依据：{reason}',
                'flags':[]})
        assessment={'items':[{k:v for k,v in x.items() if k not in ('max_score','evidence_scope')} for x in items],'review_reasons':[]}
        question={'question_id':q,'text':oldmd[q].split('## 题目')[1].split('## 满分')[0].strip(),'max_score':10}
        answer={'question_id':q,'answer_id':aid,'text':text}
        validate_input(question,dict(rubric,status='PUBLISHED'),answer)
        validate_output(json.dumps(assessment,ensure_ascii=False),rubric,answer)
        total=sum(scores);before=float(source['score'])
        entry={'question_id':q,'answer_id':aid,'answer':text,'previous_label_version':PREVIOUS,
            'label_version':VERSION,'rubric_version':rubric['version'],'previous_score':before,
            'provisional_score':total,'draft_items':items,'assessment':assessment,
            'decision_reason':reason,'status':'AI_PREPROCESSED_PENDING_TEACHER',
            'teacher_confirmed':False,'teacher_score':None,'teacher_reason':None,
            'recommended_for_reuse_after_approval':bool(text.strip()),'approved_for_reuse':False,
            'representative_answer_id':rep}
        judgments.append(entry)
        if total!=before:changes.append({k:entry[k] for k in ['answer_id','previous_score','provisional_score','decision_reason']})
    validate_checklist(rubrics['011'])
    save_rows(OUT/'数据/全部疑点逐项预处理.jsonl',judgments)
    save(OUT/'数据/实际改分清单.json',changes)
    for q,r in rubrics.items():save(OUT/f'规则/{q}_暂行机器规则.json',r)
    newmd={q:new_question_text(q,oldmd[q],rubrics[q]) for q in qpaths}
    diffs=[]
    for q in qpaths:diffs.extend(difflib.unified_diff(oldmd[q].splitlines(True),newmd[q].splitlines(True),fromfile=str(qpaths[q].relative_to(ROOT)),tofile=q+'/暂行规则'))
    mdwrite(OUT/'数据/规则修改.diff',''.join(diffs))
    for q in qpaths:mdwrite(OUT/f'规则/{q}_暂行规则与参考答案.md',newmd[q])
    lookup={r['answer_id']:r for r in judgments}
    sample_replacements={'011-000058':'011-000046','011-000063':'011-000069'}
    staged={}
    for (q,n),(path,source_rows) in datafiles.items():
        proposed=[]
        for r in source_rows:
            item=dict(r)
            if q=='011' and n in ('02','03') and item['answer_id'] in sample_replacements:
                item=dict(active[sample_replacements[item['answer_id']]])
                if n=='02':item.pop('score')
            if n in ('03','05') and item['answer_id'] in lookup:item['score']=str(lookup[item['answer_id']]['provisional_score'])
            proposed.append(item)
        proposed.sort(key=lambda r:r['answer_id'])
        staged[(q,n)]=proposed
        save_rows(OUT/f'当前暂行数据/{q}/{path.name}',proposed)
    save(OUT/'数据/学习样本调整.json',{'reason':'暂行改分后补回4和6两档，保持11条及最小替换；移出样本仍计入历史学习暴露',
        'replacements':sample_replacements,'current_total':165,'historical_learning_union':167})
    old_meta={r['answer_id']:r for r in rows(ROOT/'A任务修正_2026-11-15/数据与版本/逐条标签来源与版本.jsonl')}
    provenance=[];splits=[];stats=[]
    old_learning={r['answer_id'] for (q,n),(_,rr) in datafiles.items() if n=='03' for r in rr}
    current_learning={r['answer_id'] for (q,n),rr in staged.items() if n=='03' for r in rr}
    exposed=old_learning|current_learning
    for q in qpaths:
        current={r['answer_id']:r for r in staged[(q,'05')]}
        assert set(current_learning & set(current))=={r['answer_id'] for r in staged[(q,'03')]}
        assert {r['score'] for r in staged[(q,'03')]}=={r['score'] for r in current.values()}
        assert [{k:v for k,v in r.items() if k!='score'} for r in staged[(q,'03')]]==staged[(q,'02')]
        for g,members in groups.items():
            if g[0]!=q:continue
            gid=hashlib.sha256((q+'\0'+g[1]).encode()).hexdigest()
            learning_group=any(r['answer_id'] in exposed for r in members)
            for source in members:
                aid=source['answer_id'];p=dict(old_meta[aid]);j=lookup.get(aid)
                p.update(label_set_version=VERSION,current_reference_score=float(current[aid]['score']),
                    metadata_version='2026-11-16-ai-provisional-metadata-v1',
                    ai_preprocessed=aid in lookup,assessed_under_rubric_version=rubrics[q]['version'] if j else None,
                    rubric_document_sha256=hashfile(OUT/f'规则/{q}_暂行规则与参考答案.md'),
                    published_teacher_rubric_version=None,runtime_case_eligible=False,approved_for_case_reuse=False)
                if j and j['previous_score']!=j['provisional_score']:
                    p.update(current_score_origin='ai_provisional_revision',previous_score_origin=old_meta[aid]['current_score_origin'])
                provenance.append(p)
                role=('current_learning' if aid in current_learning else 'historical_learning' if aid in exposed else 'learning_duplicate' if learning_group else 'development_candidate')
                splits.append({'question_id':q,'answer_id':aid,'label_set_version':VERSION,'group_sha256':gid,
                    'role':role,'pool':'learning_exposed' if learning_group else 'development',
                    'development_subset':None if learning_group else 'development_'+('a' if int(gid[-1],16)%2==0 else 'b'),
                    'final_test_eligible':False,'near_duplicate_review_complete':False})
        stats.append({'question_id':q,'answers':len(current),'learning_samples':len(staged[(q,'03')]),
            'current_scores':sorted({r['score'] for r in current.values()},key=float)})
    save_rows(OUT/'数据/当前逐条标签来源与版本.jsonl',sorted(provenance,key=lambda r:r['answer_id']))
    save_rows(OUT/'数据/当前开发用途划分.jsonl',sorted(splits,key=lambda r:r['answer_id']))
    save(OUT/'数据/输入源文件摘要_应用前.json',before_hash)
    candidates=[]
    for r in judgments:
        candidates.append({'case_id':'AI-DRAFT-'+r['answer_id'],'question_id':r['question_id'],
            'rubric_version':r['rubric_version'],'case_set_version':'AI-DRAFT-20261116-v1',
            'teacher_approved':False,'approved_for_reuse':False,'status':'AI_DRAFT_PENDING_TEACHER',
            'answer':{'question_id':r['question_id'],'answer_id':r['answer_id'],'text':r['answer']},
            'assessment':r['assessment'],'proposal_reason':r['decision_reason']})
    save(OUT/'数据/已填好的校准案例候选_未教师批准.json',candidates)
    review=[]
    group_order=sorted(selected,key=lambda g:(not any(lookup[r['answer_id']]['provisional_score']!=lookup[r['answer_id']]['previous_score'] for r in groups[g]),g[0],-abs(lookup[groups[g][0]['answer_id']]['provisional_score']-lookup[groups[g][0]['answer_id']]['previous_score']),groups[g][0]['answer_id']))
    for group in group_order:
        members=groups[group];r=lookup[members[0]['answer_id']]
        ids='、'.join(x['answer_id'] for x in members)
        review.append(f'## {ids}\n\n旧分：{r["previous_score"]:g}；本次暂行分：**{r["provisional_score"]:g}**。\n\n处理结论：{r["decision_reason"]}\n\n')
        review.append('| 分项 | 暂行分/上限 |\n| --- | ---: |\n')
        for item in r['draft_items']:review.append(f'| {item["criterion_id"]} | {item["suggested_score"]}/{item["max_score"]} |\n')
        review.append('\n学生原文（同文组的空白差异见JSON，未改写学生答案）：\n\n```text\n'+r['answer']+'\n```\n\n教师只需标记：[ ]认可本处理；[ ]改为____分，分项修改____；[ ]规则需调整____。\n\n是否另行批准案例复用：[ ]是 [ ]否；审核者/日期：____。\n\n')
    header=f'# 全部疑点已处理稿：教师集中核对\n\n本次覆盖{len(targets)}条、{len(selected)}个同文组。已给出暂行分、逐项分和处理理由，不是空白评分任务。全部为AI待核验结论，历史教师/用户记录另外保留；没有新增教师签字或复用批准。\n\n'
    mdwrite(OUT/'01_全部疑点处理稿_老师核对这一份.md',header+''.join(review))
    save(OUT/'数据/试用验收口径_已拟定待核验.json',{'status':'PROPOSED_PENDING_TEACHER','score_scale':10,
        'tolerance':1,'severe_gap':3,'minimum_scored_coverage':0.95,'target_mae':1,
        'target_within_tolerance_per_reference':0.80,'maximum_severe_error_rate_per_reference':0.05,
        'target_human_audited_evidence_support_rate':0.95,'sample_low_risk_review_fraction':0.10,
        'all_flagged_answers_require_review':True,'all_draft_results_require_review':True,
        'teacher_approved':False,'note':'给教师评估的开发目标，不是实测成绩、比赛官方标准或已经证明的阈值。'})
    comparisons={}
    qtruth={r['answer_id']:dict(r) for r in datafiles[('011','05')][1]}
    for aid,r in lookup.items():
        if aid in qtruth:qtruth[aid]['score']=str(r['provisional_score'])
    for name,pp in old_predictions.items():
        results=[]
        for p in pp:
            ref=float(qtruth[p['answer_id']]['score']);delta=p['model_score']-ref
            results.append({'answer_id':p['answer_id'],'reference_score':ref,'model_score':p['model_score'],
                'difference':delta,'absolute_error':abs(delta),'label_version':VERSION,
                'truth_status':'AI provisional labels for teacher review, not independent teacher truth'})
        n=len(results);stat={'n':n,'mae':sum(x['absolute_error'] for x in results)/n,
            'rmse':(sum(x['difference']**2 for x in results)/n)**.5,
            'within_1':sum(x['absolute_error']<=1 for x in results),'severe_3':sum(x['absolute_error']>=3 for x in results),
            'model_rerun':False,'label_version':VERSION,'independent_effectiveness_claim_allowed':False}
        suffix='有得分点' if '(有得分点' in name else '无得分点'
        save_rows(OUT/f'评测复算/{suffix}_逐条对照.jsonl',results);comparisons[suffix]=stat
    save(OUT/'评测复算/两轮旧输出按暂行标签重算.json',comparisons)
    summary={'label_version':VERSION,'previous_version':PREVIOUS,'questions':len(qpaths),'answers':len(active),
        'reviewed_problem_answers':len(targets),'reviewed_normalized_groups':len(selected),
        'changed_scores':len(changes),'unchanged_problem_scores':len(targets)-len(changes),
        'rules_completed':len(rubrics),'teacher_approved_cases':0,'final_test_answers':0,
        'proposals_have_complete_items':True,'model_calls':0,'current_learning_samples':len(current_learning),
        'historical_learning_union':len(exposed),'learning_duplicate_exclusions':sum(r['role']=='learning_duplicate' for r in splits),
        'development_candidates':sum(r['pool']=='development' for r in splits),'applied_to_current_data':args.apply}
    save(OUT/'数据/执行摘要.json',summary)
    if args.apply:
        for rel,oldhash in before_hash.items():assert hashfile(ROOT/rel)==oldhash,'Concurrent source modification: '+rel
        for (q,n),(path,_) in datafiles.items():
            if n in ('02','03','05'):path.write_bytes((OUT/f'当前暂行数据/{q}/{path.name}').read_bytes())
        for q,path in qpaths.items():path.write_text(newmd[q],encoding='utf-8')
        reviews_by_id={r['answer_id']:r for r in previous_review}
        for r in judgments:
            aid=r['answer_id'];old=reviews_by_id.get(aid)
            if old is None:
                old={'answer_id':aid,'q_id':r['question_id'],'answer':r['answer'],
                    'original_score':str(r['previous_score']),'teacher_score':None,'teacher_reason':None,
                    'flags':['new_semantic_preprocess_review']};previous_review.append(old)
            old.setdefault('review_status_before_20261116',old.get('review_status'))
            old.setdefault('score_before_20261116',str(r['previous_score']))
            old.update(current_score=str(r['provisional_score']),label_version=VERSION,
                review_status='ai_preprocessed_pending_teacher',ai_proposed_score=r['provisional_score'],
                ai_decision_reason=r['decision_reason'],ai_rubric_version=r['rubric_version'],
                ai_preprocess_source='A全面预处理_2026-11-16/数据/全部疑点逐项预处理.jsonl',
                current_score_source='AI暂行修订，教师待核验' if r['provisional_score']!=r['previous_score'] else '既有分数，本轮AI复核未改分')
            if old.get('teacher_score') is not None:old['teacher_score_applies_to_current_score']=float(old['teacher_score'])==r['provisional_score']
        save_rows(DATA/'03_待教师复核记录.jsonl',previous_review)
        mdwrite(DATA/'08_本轮暂行修订入口_2026-11-16.md',f'# 当前暂行修订入口\n\n标签集：`{VERSION}`。已处理{len(targets)}条疑点、{len(selected)}组，实际改分{len(changes)}条。全部待教师核验，保留历史教师总分与用户修订来源。\n\n见[总表](../A全面修正总表_2026-11-16.md)和[老师核对稿](../A全面预处理_2026-11-16/01_全部疑点处理稿_老师核对这一份.md)。\n\n原始all.jsonl、排序后的数据、早期拆分、历史报告不变；当前学习有分样本同步新分；无分版原文不变。\n')
        usage=DATA/'00_使用说明.md';text=usage.read_text(encoding='utf-8')
        parts=[f'# 当前数据包说明\n\n当前标签集：`{VERSION}`；本轮AI暂行处理，待教师核验。已处理{len(targets)}条疑点，改分{len(changes)}条；其余记录未逐条语义重评分。详见[修订入口](08_本轮暂行修订入口_2026-11-16.md)。\n\n',
            '原1,582条学生答案及编号不变，历史教师和用户裁定保留。每题01为暂行题目规则，02/03为无分/有分学习样本，04/05为全量无分/有分答案。JSONL字段不变。\n\n',
            '011为补齐改分后缺少的4、6档，将学习样本058换为046、063换为069；总学习样本仍165条，两个移出样本仍算历史学习暴露。历史与当前学习编号并集167，再排除同文74条，开发候选1,341条；现有材料不称封存最终测试。近重复及学生身份分组尚未完成。\n\n',
            '版本索引见[当前逐条来源](../A全面预处理_2026-11-16/数据/当前逐条标签来源与版本.jsonl)和[开发用途](../A全面预处理_2026-11-16/数据/当前开发用途划分.jsonl)。前者含参考分，只用于审核/评测，不传给待评分模型。逐项预处理是AI草案，不是真实教师校准案例。\n\n',
            '来源与许可沿用[来源说明](01_数据来源与使用范围说明.md)；原始下载、早期拆分和旧实验输出不变。历史操作见[11月10日记录](../数据进一步处理.md)和[11月8日教师记录](04_教师复核修改记录.md)。初始抽样种子15543794716376506500，11月8日补样种子2026110801，本轮只作上述确定性最小替换。\n\n',
            '| 题号 | 全量 | 学习样本 | 当前分数（学习样本全部覆盖） |\n| --- | ---: | ---: | --- |\n']
        for stat in stats:parts.append(f'| {stat["question_id"]} | {stat["answers"]} | {stat["learning_samples"]} | {", ".join(stat["current_scores"])} |\n')
        newusage=''.join(parts)
        mdwrite(OUT/'数据/使用说明修改.diff',''.join(difflib.unified_diff(text.splitlines(True),newusage.splitlines(True),fromfile='00_使用说明_旧版',tofile='00_使用说明_当前版')))
        usage.write_text(newusage,encoding='utf-8')
        for source in ['A任务修正清单_2026-11-15.md','数据进一步处理.md']:
            p=ROOT/source;txt=p.read_text(encoding='utf-8');title,body=txt.split('\n',1)
            p.write_text(title+'\n\n> 2026-11-16后续：已按最新用户要求完成实质预处理，当前入口见[A全面修正总表](A全面修正总表_2026-11-16.md)。以下保留当时版本记录。\n'+body,encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print('CHANGES',json.dumps(changes,ensure_ascii=False))

if __name__=='__main__':main()
