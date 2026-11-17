"""Conservative heading scopes; not a parser or proof of student code correctness."""
import re
from .core import _require, _text

OPERATIONS = {'enqueue', 'dequeue', 'empty'}
_HEADING = re.compile(r'^\s*(?:#{1,6}\s*)?(?:[123一二三][.、．)）]\s*)?'
                      r'(入队|出队|判断队列是否为空|判断是否为空|判空)\s*(?:[:：]|$)')


def annotate_operations(units):
    operation = 'unspecified'
    result = []
    for unit in units:
        match = _HEADING.match(unit['text'])
        if match:
            operation = {'入队': 'enqueue', '出队': 'dequeue'}.get(match[1], 'empty')
        result.append(dict(unit, operation=operation))
    return result


def validate_analysis(value, resolve, text):
    fields = {'method','invariant','operations'}
    _require(isinstance(value,dict), 'algorithm_analysis must be an object')
    _require(set(value)==fields, 'algorithm_analysis fields must be exactly method, invariant, operations; '
             'unexpected: ' + ', '.join(sorted(set(value)-fields)) + '; missing: '
             + ', '.join(sorted(fields-set(value))))
    _require(value['method'] in ('STANDARD', 'ALTERNATIVE', 'UNCLEAR'), 'invalid algorithm method')
    _require(_text(value['invariant']), 'state invariant explanation required')
    _require(isinstance(value['operations'], list), 'operation analysis must be a list')
    seen, result = set(), []
    for row in value['operations']:
        _require(isinstance(row, dict) and set(row) ==
                 {'operation', 'assessment', 'reason', 'evidence'}, 'invalid operation analysis fields')
        op = row['operation']
        _require(isinstance(op, str) and op in OPERATIONS and op not in seen, 'unknown or duplicate operation')
        seen.add(op)
        _require(row['assessment'] in ('SUPPORTED', 'INCOMPLETE', 'UNCLEAR') and _text(row['reason']),
                 'invalid operation assessment')
        evidence = resolve(row['evidence'], text)
        _require(row['assessment'] != 'SUPPORTED' or evidence, 'supported operation needs source evidence')
        result.append(dict(row, evidence=evidence))
    _require(seen == OPERATIONS, 'missing operation analysis')
    return dict(value, operations=result)


def validate_operation_scope(check, spans, units, analysis):
    expected = check.get('evidence_operation')
    if expected is None:
        return
    # Whole-algorithm equivalence is evaluated separately for alternative methods.
    # A heading alone must never reject a correct eager-transfer implementation.
    if analysis and analysis['method'] == 'ALTERNATIVE':
        return
    scopes = {u['operation'] for u in units for span in spans
              if span['start'] < u['end'] and u['start'] < span['end']}
    _require(not scopes or 'unspecified' in scopes or expected in scopes,
             'evidence belongs only to another operation; inspect the required operation/branch, '
             'do not borrow enqueue transfer as dequeue evidence')
