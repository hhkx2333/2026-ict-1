"""Deterministic synthetic example. No real students or teacher labels."""
import json
from pathlib import Path


def build_fixture():
    text = '互斥：资源一次只允许一个进程使用。请求并保持。'
    criteria = []
    rows = []
    for cid, name, quote, score in [
        ('R1', '互斥', '互斥：资源一次只允许一个进程使用。', 2),
        ('R2', '请求并保持', '请求并保持', 1),
        ('R3', '不可剥夺', None, 0),
        ('R4', '循环等待', None, 0),
    ]:
        criteria.append({'criterion_id': cid, 'description': name, 'max_score': 2,
                         'allowed_scores': [0, 1, 2],
                         'rules': {cid + '-levels': '名称与含义准确2分；仅名称1分；含义准确无名称也可2分；缺失或矛盾0分。'}})
        evidence = [] if quote is None else [{'quote': quote, 'start': text.index(quote), 'end': text.index(quote) + len(quote)}]
        rows.append({'criterion_id': cid, 'suggested_score': score, 'evidence': evidence,
                     'rule_id': cid + '-levels', 'reason': '模拟：完整解释' if score == 2 else '模拟：仅名称' if score else '模拟：未发现相关表述', 'flags': []})
    return {'provenance': 'SYNTHETIC / NOT TEACHER REVIEWED / FIXTURE REPLAY ONLY',
            'question': {'question_id': 'Q01', 'text': '列出并解释死锁的四个必要条件。', 'max_score': 8},
            'rubric': {'question_id': 'Q01', 'version': 'synthetic-v1', 'status': 'PUBLISHED', 'criteria': criteria},
            'answer': {'question_id': 'Q01', 'answer_id': 'SYNTHETIC-A001', 'text': text},
            'model_output': {'items': rows, 'review_reasons': []}}


if __name__ == '__main__':
    Path(__file__).with_name('synthetic.json').write_text(json.dumps(build_fixture(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
