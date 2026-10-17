"""Bounded, loopback-only Ollama HTTP calls for the M04 teaching baseline.

Timeouts apply to socket operations, not a hard deadline for an entire request.
This adapter never retries, follows redirects, reads proxies, or executes tools.
"""
import hashlib
import http.client
import json
import math
import socket
from urllib.parse import urlsplit


MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ModelError(Exception):
    """Safe summary for display, with optional raw evidence for local reports."""

    def __init__(self, code, message, *, status=None, raw=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.raw = raw


def strict_json_loads(text):
    """Parse JSON without duplicate keys, NaN, Infinity, or overflow to infinity."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError('Non-finite JSON number')

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Non-finite JSON number')
        return number

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=nonfinite,
                          parse_float=finite_float)
    except (RecursionError, TypeError) as error:
        raise ValueError('Invalid JSON input') from error


def _model_name(model):
    if (not isinstance(model, str) or not model or len(model) > 256
            or any(character.isspace() or ord(character) < 32 for character in model)):
        raise ValueError('model must be a nonempty exact Ollama tag')
    # A local endpoint may route a cloud model. Reject known name/metadata signals.
    if model.endswith((':cloud', '-cloud')):
        raise ModelError('remote_model', 'Cloud models are outside this local baseline.')


def _reject_remote(value):
    for key in ('remote_host', 'remote_model'):
        if value.get(key):
            raise ModelError('remote_model', 'Remote model metadata is outside this local baseline.')


class OllamaClient:
    def __init__(self, base_url='http://127.0.0.1:11434', timeout_seconds=120):
        if (not isinstance(base_url, str) or any(c.isspace() for c in base_url)
                or '?' in base_url or '#' in base_url):
            raise ValueError('base_url must be a loopback HTTP URL without credentials or suffixes')
        try:
            parts = urlsplit(base_url)
            port = parts.port if parts.port is not None else 80
            host = parts.hostname
        except ValueError as error:
            raise ValueError('Invalid loopback HTTP URL') from error
        if (parts.scheme != 'http' or host not in ('127.0.0.1', '::1', 'localhost')
                or parts.username is not None or parts.password is not None
                or parts.path not in ('', '/') or not 1 <= port <= 65535):
            raise ValueError('base_url must be a loopback HTTP URL without credentials or suffixes')
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0):
            raise ValueError('timeout_seconds must be positive and finite')
        self.host = '127.0.0.1' if host == 'localhost' else host
        self.port = port
        authority = f'[{self.host}]' if ':' in self.host else self.host
        self.base_url = f'http://{authority}:{port}'
        self.timeout_seconds = timeout_seconds

    def _request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(
            payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_seconds)
        try:
            connection.request(method, path, body=body, headers={
                'Content-Type': 'application/json', 'Accept': 'application/json'})
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
        except (socket.timeout, TimeoutError) as error:
            raise ModelError('timeout', 'Ollama socket operation timed out.') from error
        except (OSError, http.client.HTTPException) as error:
            raise ModelError('connection', 'Unable to complete the Ollama HTTP connection.') from error
        finally:
            connection.close()
        if len(data) > MAX_RESPONSE_BYTES:
            raise ModelError('response_too_large', 'Ollama response exceeds 4 MiB.', status=status)
        try:
            raw = strict_json_loads(data.decode('utf-8'))
        except (ValueError, UnicodeError) as error:
            if not 200 <= status < 300:
                raise ModelError('http_error', 'Ollama returned an unsuccessful HTTP status.',
                                 status=status) from error
            raise ModelError('protocol', 'Ollama response is not valid UTF-8 JSON.', status=status) from error
        if not 200 <= status < 300:
            raise ModelError('http_error', 'Ollama returned an unsuccessful HTTP status.',
                             status=status, raw=raw)
        if not isinstance(raw, dict):
            raise ModelError('protocol', 'Ollama response must be a JSON object.', status=status, raw=raw)
        if 'error' in raw:
            raise ModelError('server_error', 'Ollama reported an error in its response.', status=status, raw=raw)
        return raw

    def inventory(self, model):
        """Record the exact local tag, digest, and available server/model metadata."""
        _model_name(model)
        version = self._request('GET', '/api/version')
        if not isinstance(version.get('version'), str) or not version['version'].strip():
            raise ModelError('protocol', 'Missing Ollama server version.', raw=version)
        tags = self._request('GET', '/api/tags')
        models = tags.get('models')
        if not isinstance(models, list) or any(not isinstance(item, dict) for item in models):
            raise ModelError('protocol', 'Invalid Ollama model list.', raw=tags)
        matches = [item for item in models if item.get('name') == model]
        if not matches:
            raise ModelError('model_not_found', 'Exact model tag is absent from Ollama inventory.')
        if len(matches) != 1:
            raise ModelError('protocol', 'Ollama inventory contains duplicate model tags.', raw=tags)
        tag = matches[0]
        _reject_remote(tag)
        digest = tag.get('digest')
        if not isinstance(digest, str) or not digest.strip():
            raise ModelError('protocol', 'Missing Ollama model digest.', raw=tag)
        show = self._request('POST', '/api/show', {'model': model})
        _reject_remote(show)
        template = show.get('template')
        if template is not None and not isinstance(template, str):
            raise ModelError('protocol', 'Invalid Ollama template metadata.', raw=show)
        return {
            'model': model, 'digest': digest, 'server_version': version['version'],
            'details': show.get('details'), 'capabilities': show.get('capabilities'),
            'template_sha256': hashlib.sha256(template.encode('utf-8')).hexdigest()
                if template is not None else None,
            'show': show,
        }

    def chat(self, model, messages, *, format_schema, options, think=None):
        """Return raw non-streaming output; scoring handles empty/truncated answers."""
        _model_name(model)
        if think is not None and type(think) is not bool:
            raise ValueError('think must be None, True, or False')
        if not isinstance(format_schema, dict) or not isinstance(options, dict):
            raise ValueError('format_schema and options must be objects')
        if (not isinstance(messages, list) or not messages
                or any(not isinstance(message, dict)
                       or message.get('role') not in ('system', 'user', 'assistant')
                       or not isinstance(message.get('content'), str) for message in messages)):
            raise ValueError('messages must contain role/content objects')
        payload = {'model': model, 'messages': messages, 'format': format_schema,
                   'options': options, 'stream': False, 'keep_alive': '5m'}
        if think is not None:
            payload['think'] = think
        raw = self._request('POST', '/api/chat', payload)
        _reject_remote(raw)
        message = raw.get('message')
        if (raw.get('model') != model or raw.get('done') is not True
                or not isinstance(message, dict) or message.get('role') != 'assistant'
                or not isinstance(message.get('content'), str) or message.get('tool_calls')):
            raise ModelError('protocol', 'Unexpected Ollama chat response envelope.', raw=raw)
        return raw
