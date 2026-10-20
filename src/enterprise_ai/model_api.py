"""Loopback-only authenticated API contract probe; never persist credentials or response bodies."""
import argparse
import http.client
import json
import os
from pathlib import Path
import sys

from .baseline import SYSTEM_PROMPT, score_response
from .ollama import MAX_RESPONSE_BYTES, OllamaClient, strict_json_loads

EXPECTED = {'intent': 'leave_balance', 'customer_id': None, 'product_id': None, 'action': 'query'}


class ProbeError(Exception):
    pass


def _request(client, method, path, key=None, payload=None):
    connection = http.client.HTTPConnection(client.host, client.port, timeout=client.timeout_seconds)
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProbeError('response_size')
        if 300 <= response.status < 400:
            raise ProbeError('redirect')
        # Do not parse error bodies, which can echo secrets or contain HTML.
        value = strict_json_loads(raw.decode('utf-8')) if response.status == 200 else None
        return response.status, value
    finally:
        connection.close()


def _completion_score(value, model):
    if not isinstance(value, dict) or value.get('model') != model:
        raise ProbeError('completion_model')
    choices = value.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ProbeError('completion_choices')
    choice = choices[0]
    message = choice.get('message')
    if (type(choice.get('index')) is not int or choice['index'] != 0
            or choice.get('finish_reason') not in ('stop', 'length')
            or not isinstance(message, dict) or message.get('role') != 'assistant'
            or not isinstance(message.get('content'), str) or message.get('tool_calls')):
        raise ProbeError('completion_contract')
    usage = value.get('usage')
    fields = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    if (not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in fields)
            or usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens']):
        raise ProbeError('completion_usage')
    return score_response({'message': {'content': message['content']},
                           'done_reason': choice['finish_reason']}, EXPECTED)


def probe(base_url, model, output, *, api_key, timeout=120):
    client = OllamaClient(base_url=base_url, timeout_seconds=timeout)
    if (not isinstance(api_key, str) or not api_key or len(api_key) > 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in api_key)):
        raise ValueError('API_KEY must be nonempty printable ASCII without whitespace')
    if (not isinstance(model, str) or not model or len(model) > 256
            or any(c.isspace() or ord(c) < 32 for c in model)):
        raise ValueError('Invalid model alias')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve before the first request so competing runs cannot overwrite a report.
    with output.open('x', encoding='utf-8') as stream:
        report = {'status': 'failed', 'unauthenticated_rejected': False, 'error': None, 'score': None}
        try:
            status, _ = _request(client, 'GET', '/v1/models')
            if status not in (401, 403):
                raise ProbeError('authentication_required')
            report['unauthenticated_rejected'] = True
            status, value = _request(client, 'GET', '/v1/models', api_key)
            if status != 200:
                raise ProbeError('authenticated_models_status')
            if (not isinstance(value, dict) or not isinstance(value.get('data'), list)
                    or not any(isinstance(item, dict) and item.get('id') == model for item in value['data'])):
                raise ProbeError('model_alias')
            payload = {'model': model, 'messages': [{'role': 'system', 'content': SYSTEM_PROMPT},
                        {'role': 'user', 'content': '请查询我本人的剩余假期余额。'}],
                       'stream': False, 'temperature': 0, 'max_tokens': 256}
            status, value = _request(client, 'POST', '/v1/chat/completions', api_key, payload)
            if status != 200:
                raise ProbeError('completion_status')
            report['score'] = _completion_score(value, model)
            if not report['score']['exact_match']:
                raise ProbeError('contract_answer')
            report['status'] = 'passed'
        except ProbeError as exc:
            report['error'] = str(exc)
        except (ValueError, OSError, http.client.HTTPException, TypeError, KeyError, RecursionError):
            report['error'] = 'transport_or_payload'
        # Only fixed codes/booleans/scores, never URLs, aliases, raw answers or exceptions.
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='M07 loopback model service probe')
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('probe')
    command.add_argument('--base-url', default='http://127.0.0.1:8000')
    command.add_argument('--model', required=True)
    command.add_argument('--output', type=Path, required=True)
    command.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args(argv)
    try:
        report = probe(args.base_url, args.model, args.output, api_key=os.environ.get('API_KEY', ''),
                       timeout=args.timeout)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['status'] == 'passed' else 2
    except (ValueError, OSError):
        print('Invalid arguments, missing API_KEY, or unavailable output path.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
