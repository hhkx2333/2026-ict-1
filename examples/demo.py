"""Run from project root: python3 -m examples.demo [--live]."""
import argparse
import json
from pathlib import Path
from backend.grading import grade_answer, FixtureProvider, ChatCompletionsProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Send synthetic input to explicitly configured model service')
    args = parser.parse_args()
    fixture = json.loads(Path(__file__).with_name('synthetic.json').read_text(encoding='utf-8'))
    provider = ChatCompletionsProvider.from_env() if args.live else FixtureProvider(fixture['model_output'])
    result = grade_answer(fixture['question'], fixture['rubric'], fixture['answer'], [], provider=provider)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result['suggested_total'] is not None else 1


if __name__ == '__main__':
    raise SystemExit(main())
