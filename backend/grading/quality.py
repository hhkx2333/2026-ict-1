"""Offline paired comparisons and explicit human audits; never infer semantic truth."""
import hashlib
import json
import math
from .core import ValidationError, _require, _text
from .datasets import index_rows
from .evaluation import evaluate


def assessment_fingerprint(prediction):
    fields = {k: prediction.get(k) for k in
              ('question_id','answer_id','rubric_version','case_set_version','items','suggested_total')}
    return hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def evidence_tasks(predictions):
    tasks = []
    for p in predictions:
        for item in p.get('items', []):
            tasks.append({'answer_id': p['answer_id'], 'criterion_id': item['criterion_id'],
                          'assessment_sha256': assessment_fingerprint(p),
                          'suggested_score': item['suggested_score'], 'reason': item['reason'],
                          'evidence': item['evidence'], 'teacher_confirmed': False,
                          'verdict': None, 'reviewer_id': None, 'review_note': None})
    return tasks


def evaluate_quality(predictions, truth, *, evidence_reviews=(), time_logs=(), maximum=10,
                     tolerance=1, severe_gap=3):
    base = evaluate(predictions, truth, maximum=maximum, tolerance=tolerance, severe_gap=severe_gap)
    predicted, labels = index_rows(predictions), index_rows(truth)
    all_items, by_item = set(), {}
    for aid, p in predicted.items():
        for item in p.get('items', []):
            key = (aid, item['criterion_id'])
            _require(key not in by_item, 'duplicate prediction criterion')
            all_items.add(key); by_item[key] = item
    checked, supported, uncertain, seen = 0, 0, 0, set()
    for review in evidence_reviews:
        _require(isinstance(review, dict), 'invalid evidence review')
        _require(_text(review.get('answer_id')) and _text(review.get('criterion_id')), 'invalid review identity')
        key = (review['answer_id'], review['criterion_id'])
        _require(key in all_items and key not in seen, 'unknown or duplicate evidence review')
        seen.add(key)
        _require(review.get('teacher_confirmed') is True and _text(review.get('reviewer_id')),
                 'evidence semantics require explicit human review')
        _require(review.get('assessment_sha256') == assessment_fingerprint(predicted[key[0]]),
                 'evidence review belongs to a different model assessment')
        _require(review.get('verdict') in ('SUPPORTED','UNSUPPORTED','UNCERTAIN'), 'invalid evidence verdict')
        checked += 1
        supported += review['verdict'] == 'SUPPORTED'
        uncertain += review['verdict'] == 'UNCERTAIN'
    criterion_differences, expected_items = [], 0
    from .datasets import score_number
    for aid, label in labels.items():
        if 'confirmed_items' not in label:
            continue
        _require(label.get('teacher_confirmed') is True and isinstance(label['confirmed_items'], list),
                 'criterion quality requires teacher-confirmed per-item truth')
        cids = set()
        for item in label['confirmed_items']:
            cid = item.get('criterion_id')
            _require(_text(cid) and cid not in cids, 'invalid truth criterion')
            cids.add(cid); expected_items += 1
            item_max = score_number(item.get('max_score'), maximum)
            _require(item_max > 0, 'positive teacher criterion maximum required')
            ref = score_number(item.get('score'), item_max)
            if (aid,cid) in by_item:
                criterion_differences.append(score_number(by_item[(aid,cid)]['suggested_score'], item_max)-ref)
    phases = {'rule_preparation','calibration','review','spot_check','rework','manual_baseline'}
    seconds, time_ids = {}, set()
    for log in time_logs:
        _require(isinstance(log, dict) and _text(log.get('event_id')) and log['event_id'] not in time_ids,
                 'unique time event ID required')
        _require(log.get('phase') in phases and _text(log.get('reviewer_id')), 'invalid human time phase/reviewer')
        value = log.get('seconds')
        _require(type(value) in (int,float) and math.isfinite(value) and value >= 0, 'invalid human seconds')
        time_ids.add(log['event_id'])
        seconds[log['phase']] = seconds.get(log['phase'], 0) + value
    severe = {aid for aid,p in predicted.items() if p.get('status') != 'FAILED'
              and p.get('suggested_total',p.get('score')) is not None
              and abs(score_number(p.get('suggested_total',p.get('score')),maximum) -
                      score_number(labels[aid]['score'],maximum)) >= severe_gap}
    # All draft suggestions still go to teachers. This diagnostic is NOT deployment leakage.
    severe_unflagged = []
    explicit_flag_records = 0
    for aid, p in predicted.items():
        risks = p.get('metadata', {}).get('risk_codes')
        if isinstance(risks,list):
            explicit_flag_records += 1
            if aid in severe and not risks:
                severe_unflagged.append(aid)
    counts = base['all_model_suggestions']
    base.update(normalized_mae=counts['mae']/maximum if counts['mae'] is not None else None,
                within_tolerance_per_reference=counts['within_tolerance_count']/len(labels) if labels else None,
                criterion_quality={'teacher_labeled_items': expected_items, 'matched_items': len(criterion_differences),
                    'mae': sum(abs(v) for v in criterion_differences)/len(criterion_differences) if criterion_differences else None},
                evidence_quality={'total_predicted_items':len(all_items), 'human_reviewed_items':checked,
                    'supported_items':supported, 'uncertain_items':uncertain,
                    'support_rate_among_reviewed':supported/checked if checked else None,
                    'human_review_coverage':checked/len(all_items) if all_items else None},
                human_time={'recorded_events':len(time_ids), 'seconds_by_phase':seconds,
                    'assisted_total_seconds':sum(v for k,v in seconds.items() if k!='manual_baseline') if any(k!='manual_baseline' for k in seconds) else None,
                    'manual_baseline_seconds':seconds.get('manual_baseline'),
                    'note':'Measured logs only; unmatched cohorts or missing phases do not establish time savings.'},
                semantic_flag_diagnostic={'records_with_explicit_risk_codes':explicit_flag_records,
                    'severe_without_semantic_flag_ids':sorted(severe_unflagged),
                    'note':'Separate from actual SUGGESTED/REVIEW_REQUIRED routing; draft results all require review.'})
    return base


def compare_predictions(left, right, truth, **thresholds):
    """Paired IDs and explicit null coverage, no dropping failures to improve means."""
    a,b,t = index_rows(left),index_rows(right),index_rows(truth)
    _require(set(a)==set(b), 'paired methods must contain identical answer IDs')
    _require(set(a)<=set(t), 'predictions contain unknown truth IDs')
    valid = [aid for aid in a if a[aid].get('suggested_total') is not None and
             b[aid].get('suggested_total') is not None and
             a[aid].get('status')!='FAILED' and b[aid].get('status')!='FAILED']
    full = {'left':evaluate_quality(left,truth,**thresholds),'right':evaluate_quality(right,truth,**thresholds)}
    paired_truth = [t[aid] for aid in valid]
    paired_a = evaluate([a[aid] for aid in valid],paired_truth,**thresholds)['all_model_suggestions']
    paired_b = evaluate([b[aid] for aid in valid],paired_truth,**thresholds)['all_model_suggestions']
    return {'all_reference_metrics':full, 'joint_valid_count':len(valid),
            'joint_valid_left':paired_a, 'joint_valid_right':paired_b,
            'mae_change_on_joint_valid':paired_b['mae']-paired_a['mae'] if valid else None,
            'note':'Joint-valid comparison is supplementary; full reference coverage and failures remain above.'}
