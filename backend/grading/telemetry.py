"""Bounded per-job accounting without storing credentials or raw model bodies."""
import copy
import hashlib
import json
import time
from .core import _require


class MeteredProvider:
    def __init__(self, inner, max_calls=2):
        _require(type(max_calls) is int and 1 <= max_calls <= 10000, 'invalid call budget')
        self.inner, self.max_calls, self.calls = inner, max_calls, []
        self.model = getattr(inner, 'model', 'unspecified')
        self.align_unique_quotes = getattr(inner, 'align_unique_quotes', False)

    @property
    def last_usage(self):
        return getattr(self.inner, "last_usage", None)

    @property
    def last_response(self):
        return getattr(self.inner, "last_response", None)

    def complete(self, system, payload):
        _require(len(self.calls) < self.max_calls, 'model call budget exhausted')
        record = {'call': len(self.calls) + 1,
                  'request_sha256': hashlib.sha256(json.dumps({'system':system,'payload':payload},
                      ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()}
        self.calls.append(record)
        tick = time.monotonic()
        try:
            output = self.inner.complete(system, copy.deepcopy(payload))
            record['provider_returned'] = True
            return output
        except Exception as exc:
            record.update(provider_returned=False, error_type=type(exc).__name__)
            raise
        finally:
            record['seconds'] = round(time.monotonic()-tick, 6)
            usage = getattr(self.inner, 'last_usage', None)
            record['usage'] = {k: v for k, v in (usage.items() if isinstance(usage, dict) else [])
                               if type(v) is int and v >= 0 and k in
                               ('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')}
            details = getattr(self.inner, 'last_response', None)
            record['response'] = {k:v for k,v in (details.items() if isinstance(details, dict) else [])
                                  if k in ('id','model','finish_reason','error_code','error_type') and isinstance(v, str) and len(v) <= 200}
            if isinstance(details,dict) and type(details.get('http_status')) is int:
                record['response']['http_status'] = details['http_status']

    def summary(self):
        usage = {}
        for row in self.calls:
            for key, value in row['usage'].items():
                usage[key] = usage.get(key, 0) + value
        return {'model_calls': len(self.calls), 'usage_reported_calls': sum(bool(r['usage']) for r in self.calls),
                'usage_totals': usage, 'seconds': round(sum(r['seconds'] for r in self.calls), 6),
                'note': 'API-reported token usage; missing usage is unknown, not zero cost.'}
