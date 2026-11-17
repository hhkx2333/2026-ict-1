"""Explicit live batch run. Each job has a bounded call budget and append-only output."""
import argparse
import json
import time
from pathlib import Path
from .core import grade_answer, ValidationError
from .providers import ChatCompletionsProvider
from .datasets import read_jsonl, index_rows, write_json
from .baselines import grade_baseline
from .evaluation import evaluate
from .quality import evaluate_quality, compare_predictions
from .checklist import grade_checked_answer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    for field in ('question', 'answers', 'output'):
        run.add_argument('--' + field, required=True)
    run.add_argument('--mode', choices=['direct', 'fewshot', 'structured', 'calibrated', 'checklist'], required=True)
    run.add_argument('--rubric')
    run.add_argument('--examples')
    run.add_argument('--cases')
    run.add_argument('--allow-draft', action='store_true')
    run.add_argument('--live', action='store_true', help='Explicitly send these inputs to GRADING_ENDPOINT')
    run.add_argument('--limit', type=int, default=1)
    run.add_argument('--max-attempts', type=int, choices=[1, 2], default=2)
    ev = commands.add_parser('evaluate')
    for field in ('predictions', 'truth', 'output'):
        ev.add_argument('--' + field, required=True)
    ev.add_argument('--evidence-reviews')
    ev.add_argument('--time-logs')
    ev.add_argument('--compare-to')
    ev.add_argument('--maximum', type=float, default=10)
    args = parser.parse_args()
    if args.command == 'evaluate':
        rows = read_jsonl(args.predictions)
        truth = read_jsonl(args.truth)
        report = evaluate_quality(rows, truth, maximum=args.maximum,
            evidence_reviews=read_jsonl(args.evidence_reviews) if args.evidence_reviews else [],
            time_logs=read_jsonl(args.time_logs) if args.time_logs else [])
        if args.compare_to:
            report['paired_comparison'] = compare_predictions(read_jsonl(args.compare_to), rows, truth, maximum=args.maximum)
        write_json(args.output, report)
        print('Saved evaluation; reference labels never entered a scoring call.')
        return
    if not args.live:
        parser.error('run requires --live; offline verification is python3 -m examples.verify_repository_data')
    if not 1 <= args.limit <= 200:
        parser.error('--limit must be 1..200')
    if args.mode in ('structured', 'calibrated', 'checklist') and not args.rubric:
        parser.error('structured/calibrated/checklist mode requires --rubric')
    if args.mode == 'fewshot' and not args.examples:
        parser.error('fewshot requires --examples')
    if args.mode == 'calibrated' and not args.cases:
        parser.error('calibrated requires --cases with teacher-approved cases')
    if args.mode != 'fewshot' and args.examples or args.mode not in ('calibrated', 'checklist') and args.cases:
        parser.error('examples/cases supplied to the wrong mode')
    question = json.loads(Path(args.question).read_text(encoding='utf-8'))
    inputs = read_jsonl(args.answers)
    index_rows(inputs)
    for answer in inputs:
        if set(answer) != {'answer_id', 'q_id', 'answer'} or answer['q_id'] != question['question_id']:
            parser.error('answers must be matching, unscored ADS rows; label fields are forbidden')
    rubric = json.loads(Path(args.rubric).read_text()) if args.rubric else None
    cases = json.loads(Path(args.cases).read_text()) if args.cases else []
    if args.mode == 'calibrated' and not cases:
        parser.error('calibrated mode requires nonempty approved cases')
    examples = read_jsonl(args.examples) if args.examples else []
    provider = ChatCompletionsProvider.from_env()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a previous model run. No implicit resume across changed versions.
    with output.open('x', encoding='utf-8') as handle:
        for row in inputs[:args.limit]:
            answer = {'question_id': row['q_id'], 'answer_id': row['answer_id'], 'text': row['answer']}
            start = time.monotonic()
            try:
                if args.mode in ('direct', 'fewshot'):
                    result = grade_baseline(question, answer, provider=provider, examples=examples, max_attempts=args.max_attempts,
                                            rubric=rubric, allow_draft=args.allow_draft)
                else:
                    grader = grade_checked_answer if args.mode == 'checklist' else grade_answer
                    result = grader(question, rubric, answer, cases, provider=provider,
                                          max_attempts=args.max_attempts, allow_draft=args.allow_draft)
            except ValidationError:
                raise  # Invalid configuration is not a zero score or a retryable job.
            result['metadata']['mode'] = args.mode
            result['metadata']['elapsed_seconds'] = round(time.monotonic() - start, 3)
            handle.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
            handle.flush()
            print(row['answer_id'], result['status'], result['suggested_total'])


if __name__ == '__main__':
    main()
