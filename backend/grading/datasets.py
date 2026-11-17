"""Read ADS JSONL, validate paired files and split duplicate groups without labels."""
import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path
from .core import ValidationError
from .calibration import text_key


def read_jsonl(path):
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValidationError(f'{Path(path).name}:{n}: invalid JSON') from exc
            if not isinstance(row, dict):
                raise ValidationError(f'line {n} is not an object')
            rows.append(row)
    return rows


def index_rows(rows):
    result = {}
    for row in rows:
        aid = row.get('answer_id')
        if not isinstance(aid, str) or not aid or aid in result:
            raise ValidationError('missing or duplicate answer_id')
        result[aid] = row
    return result


def score_number(value, maximum=10):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValidationError('invalid score type')
    try:
        score = Decimal(str(value))
    except Exception as exc:
        raise ValidationError('invalid score') from exc
    if not score.is_finite() or not 0 <= score <= maximum:
        raise ValidationError('score out of range')
    return float(score)


def audit_pairs(unscored, scored, learning):
    plain, labeled, samples = map(index_rows, (unscored, scored, learning))
    if set(plain) != set(labeled) or not set(samples) <= set(plain):
        raise ValidationError('paired IDs or learning IDs mismatch')
    for aid, row in plain.items():
        if set(row) != {'answer_id', 'q_id', 'answer'} or not isinstance(row['answer'], str):
            raise ValidationError('unscored rows must contain exactly ID, question ID and answer text')
        if {k: v for k, v in labeled[aid].items() if k != 'score'} != row:
            raise ValidationError('paired answer content mismatch')
        score_number(labeled[aid].get('score'))
    for aid, row in samples.items():
        if row != labeled[aid]:
            raise ValidationError('learning rows do not match current labels')
    groups = {}
    for aid, row in plain.items():
        groups.setdefault(text_key(row['answer']), []).append(aid)
    learning_keys = {text_key(row['answer']) for row in samples.values()}
    excluded = sorted(aid for aid, row in plain.items()
                      if aid not in samples and text_key(row['answer']) in learning_keys)
    return {'answers': len(plain), 'learning_answers': len(samples),
            'unique_whitespace_groups': len(groups),
            'empty_answer_ids': sorted(aid for aid, r in plain.items() if not r['answer'].strip()),
            'duplicate_groups': [ids for ids in groups.values() if len(ids) > 1],
            'learning_duplicate_exclusions': excluded,
            'eligible_unseen_ids': sorted(set(plain) - set(samples) - set(excluded)),
            'note': '仅去除空白并忽略大小写的同文分组；未进行语义近重复检测。第011题已分析，属于开发资料。'}


def split_unseen(unscored, learning, seed=20261108):
    """Current Q011 splits are development subsets, never an untouched final test."""
    excluded_ids = set(index_rows(learning))
    keys = {text_key(r['answer']) for r in learning}
    groups = {}
    for row in unscored:
        if row['answer_id'] not in excluded_ids and text_key(row['answer']) not in keys:
            groups.setdefault(text_key(row['answer']), []).append(row)
    ordered = sorted(groups)
    random.Random(seed).shuffle(ordered)
    midpoint = len(ordered) // 2
    return {name: [r for key in part for r in groups[key]]
            for name, part in [('development', ordered[:midpoint]), ('development_check', ordered[midpoint:])]}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in rows), encoding='utf-8')


def snapshot_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
