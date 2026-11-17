"""Audit pinned Q011 snapshot, prepare label-separated inputs, recompute past baseline."""
import json
import re
import argparse
import subprocess
from pathlib import Path
from backend.grading.datasets import read_jsonl, audit_pairs, split_unseen, write_json, write_jsonl, snapshot_hash
from backend.grading.evaluation import evaluate

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'work/source-snapshot'
DEST = ROOT / 'work/q011'
SNAPSHOT = '4650e5eb65e44ee0bf084667efde51b1a307a900'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=SOURCE)
    args = parser.parse_args()
    source_root = args.source_root
    required = ['阅卷Agent_按题整理/011/' + name for name in
                ('01_题目与参考答案.md', '03_学习样本10%_有得分.jsonl',
                 '04_全部答案_无得分.jsonl', '05_全部答案_有得分.jsonl')]
    required.append('在正常大模型里面测试改卷能力/011_评分对比分析/011_模型批改原始结果.json')
    for rel in required:
        target = source_root / rel
        if target.exists():
            continue
        result = subprocess.run(['git', 'show', SNAPSHOT + ':' + rel], cwd=ROOT, capture_output=True)
        if result.returncode:
            parser.error('缺少固定数据快照。请先运行 git fetch origin 测试数据，或用 --source-root 指向保存的快照目录。')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.stdout)
    source = source_root / '阅卷Agent_按题整理/011'
    plain = read_jsonl(source / '04_全部答案_无得分.jsonl')
    truth = read_jsonl(source / '05_全部答案_有得分.jsonl')
    learning = read_jsonl(source / '03_学习样本10%_有得分.jsonl')
    audit = audit_pairs(plain, truth, learning)
    markdown = (source / '01_题目与参考答案.md').read_text()
    question = {'question_id': '011', 'text': markdown.split('## 题目')[1].split('## 满分')[0].strip(),
                'max_score': 10, 'reference': markdown.split('## reference1')[1].split('## reference2')[0].strip()}
    write_json(DEST / 'runtime/question.json', question)
    write_jsonl(DEST / 'runtime/calibration_examples.jsonl', learning)
    for name, rows in split_unseen(plain, learning).items():
        write_jsonl(DEST / f'runtime/{name}_unscored.jsonl', rows)
    eligible = set(audit['eligible_unseen_ids'])
    write_jsonl(DEST / 'runtime/remaining_unscored.jsonl', [r for r in plain if r['answer_id'] in eligible])
    write_jsonl(DEST / 'evaluator/current_labels.jsonl', truth)
    report_dir = source_root / '在正常大模型里面测试改卷能力/011_评分对比分析'
    historical = json.loads((report_dir / '011_模型批改原始结果.json').read_text())
    write_jsonl(DEST / 'evaluator/historical_predictions.jsonl', historical)
    metrics = evaluate(historical, truth)
    write_json(DEST / 'audit.json', audit)
    write_json(DEST / 'historical_baseline_recomputed.json', metrics)
    write_json(DEST / 'provenance.json', {
        'repository': 'hhkx2333/2026-ict-1', 'branch': '测试数据', 'commit': SNAPSHOT,
        'processing': 'Snapshot from explicit local files or git show at pinned commit; compare parsed records. Earlier local text files may normalize CRLF to LF.',
        'files': {str(p.relative_to(source_root)): snapshot_hash(p) for p in source_root.rglob('*') if p.is_file()},
        'scope': 'Q011 is development data, not a sealed final test; historical model not rerun.'})
    print(json.dumps({'audit': audit, 'historical_all_suggestions': metrics['all_model_suggestions']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
