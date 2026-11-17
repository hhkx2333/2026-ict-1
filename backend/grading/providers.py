"""Explicit providers: offline fixtures and opt-in compatible chat-completions API."""
import json
import os
import re
from .core import ValidationError
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('model endpoint redirects are not allowed')


def urlopen(request, timeout):
    # Do not forward a Bearer key to a redirected destination.
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


class FixtureProvider:
    """Replays a fixed model output; does NOT evaluate answer semantics."""
    model = 'fixture-replay-not-a-real-model'

    def __init__(self, output):
        self.output = json.dumps(output, ensure_ascii=False, allow_nan=False)

    def complete(self, system, payload):
        return self.output


def load_local_config(path=None):
    """Load only known settings; never execute a .env as shell code or overwrite env."""
    path = Path(path) if path else Path(__file__).resolve().parents[2] / '.env'
    values = dict(os.environ)
    keys = {'GRADING_ENDPOINT', 'GRADING_MODEL', 'GRADING_API_KEY',
            'GRADING_TIMEOUT', 'GRADING_MAX_TOKENS', 'GRADING_THINKING', 'GRADING_ALIGN_QUOTES'}
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('\"', "'"):
                value = value[1:-1]
            if key in keys and not values.get(key):
                values[key] = value
    return values


class ChatCompletionsProvider:
    """Use only after verifying the model service supports this protocol.

    endpoint is a full HTTPS /chat/completions URL; no provider is auto-selected.
    No streaming, tools, hidden retries, or service-specific authentication.
    """
    def __init__(self, endpoint, model, api_key, timeout=30, max_tokens=4096, thinking=None, align_quotes=False):
        parsed = urlparse(endpoint)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('endpoint must be HTTPS without embedded credentials')
        if not model or not api_key or not 0 < timeout <= 120:
            raise ValueError('model, API key and timeout (0,120] are required')
        if type(max_tokens) is not int or not 64 <= max_tokens <= 4096:
            raise ValueError('max_tokens must be 64..4096')
        if thinking not in (None, 'disabled', 'enabled'):
            raise ValueError('invalid thinking setting')
        if thinking is not None and parsed.hostname != 'api.deepseek.com':
            raise ValueError('thinking option is supported only for official DeepSeek endpoint')
        self.endpoint, self.model, self.api_key, self.timeout = endpoint, model, api_key, timeout
        self.max_tokens, self.thinking, self.last_usage = max_tokens, thinking, None
        self.last_response = None
        self.align_unique_quotes = align_quotes

    @classmethod
    def from_env(cls, env_file=None):
        config = load_local_config(env_file)
        return cls(config['GRADING_ENDPOINT'], config['GRADING_MODEL'], config['GRADING_API_KEY'],
                   timeout=float(config.get('GRADING_TIMEOUT', '30')),
                   max_tokens=int(config.get('GRADING_MAX_TOKENS', '4096')),
                   thinking=config.get('GRADING_THINKING') or None,
                   align_quotes=config.get('GRADING_ALIGN_QUOTES', 'false').lower() == 'true')

    def complete(self, system, payload):
        self.last_usage, self.last_response = None, None
        body = {'model': self.model, 'temperature': 0,
                'messages': [{'role': 'system', 'content': system},
                             {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                'response_format': {'type': 'json_object'}, 'max_tokens': self.max_tokens}
        if self.thinking:
            body['thinking'] = {'type': self.thinking}
        request = Request(self.endpoint, data=json.dumps(body).encode(), method='POST',
                          headers={'Authorization': 'Bearer ' + self.api_key,
                                   'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
        except HTTPError as exc:
            self.last_response = {'http_status': exc.code}
            try:
                error = json.loads(exc.read(16000)).get('error', {})
                for field in ('code', 'type'):
                    value = error.get(field)
                    if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',value):
                        self.last_response['error_'+field] = value
            except Exception:
                pass
            raise
        if len(raw) > 1_000_000:
            raise ValueError('model response too large')
        decoded = json.loads(raw)
        self.last_usage = decoded.get('usage')
        choice = decoded['choices'][0]
        self.last_response = {k: decoded[k] for k in ('id', 'model') if isinstance(decoded.get(k), str)}
        self.last_response['finish_reason'] = choice.get('finish_reason')
        if choice.get('finish_reason') == 'length':
            raise ValidationError('MODEL_OUTPUT_TRUNCATED: response exceeded output token limit')
        return choice['message']['content']
