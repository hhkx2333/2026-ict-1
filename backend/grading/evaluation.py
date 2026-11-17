"""Strict score alignment; all suggestions and review coverage reported separately."""
import math
from .core import ValidationError
from .datasets import index_rows, score_number


def evaluate(predictions, truth, *, maximum=10, tolerance=1, severe_gap=3):
    if not math.isfinite(maximum) or maximum <= 0 or not 0 <= tolerance <= maximum or not 0 <= severe_gap <= maximum:
        raise ValidationError('invalid evaluation thresholds')
    labeled, predicted = index_rows(truth), index_rows(predictions)
    extras = sorted(set(predicted) - set(labeled))
    if extras:
        raise ValidationError('unexpected prediction IDs: ' + ', '.join(extras[:3]))
    scored, low_risk, failures, reviews = [], [], [], []
    missing = sorted(set(labeled) - set(predicted))
    per_answer = []
    for aid, label in labeled.items():
        reference = score_number(label['score'], maximum)
        p = predicted.get(aid)
        if p is None:
            continue
        if p.get('status') == 'FAILED':
            failures.append(aid)
            continue
        value = p.get('suggested_total', p.get('score'))
        if p.get('status') == 'REVIEW_REQUIRED':
            reviews.append(aid)
        if value is None:
            if p.get('status') != 'REVIEW_REQUIRED':
                raise ValidationError('missing prediction score without failed/review status')
            continue
        value = score_number(value, maximum)
        delta = value - reference
        scored.append(delta)
        if p.get('status') == 'SUGGESTED':
            low_risk.append(delta)
        per_answer.append({'answer_id': aid, 'reference': reference, 'prediction': value, 'difference': delta})

    def summary(values):
        n = len(values)
        return {'n': n, 'mae': sum(abs(v) for v in values) / n if n else None,
                'rmse': math.sqrt(sum(v*v for v in values) / n) if n else None,
                'bias': sum(values) / n if n else None,
                'exact_count': sum(abs(v) < 1e-9 for v in values),
                'within_tolerance_count': sum(abs(v) <= tolerance for v in values),
                'within_tolerance_rate': sum(abs(v) <= tolerance for v in values) / n if n else None,
                'severe_count': sum(abs(v) >= severe_gap for v in values)}
    return {'reference_count': len(labeled), 'scored_coverage': len(scored) / len(labeled) if labeled else None,
            'all_model_suggestions': summary(scored), 'low_risk_suggestions': summary(low_risk),
            'low_risk_coverage': len(low_risk) / len(labeled) if labeled else None,
            'review_count': len(reviews), 'failed_ids': failures, 'missing_ids': missing,
            'tolerance': tolerance, 'severe_gap': severe_gap, 'score_scale': maximum,
            'largest_disagreements': sorted(per_answer, key=lambda r: -abs(r['difference']))[:15],
            'interpretation': '与当前基准的一致性，不等于模型正确率；基准标签及引文语义需人工复核。'}
