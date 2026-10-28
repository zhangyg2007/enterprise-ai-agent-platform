"""Real HTTP acceptance for a NEW synthetic M12 lab (consumes 3 days once)."""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4


def verify(base_url, credentials, *, thread_id):
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('local HTTP origin required; use an SSH tunnel for a remote server')
    origin = base_url.rstrip('/')
    # Refuse redirect/proxy forwarding of Authorization headers.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    evidence = []

    def call(method, path, name=None, body=None, expected=200):
        headers = {'Content-Type': 'application/json'}
        if name:
            headers['Authorization'] = 'Bearer ' + credentials[name]
        req = urllib.request.Request(origin+path, headers=headers, method=method,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            response = opener.open(req, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status, payload = response.status, json.load(response)
            request_id = response.headers.get('x-request-id')
        if status != expected or not request_id:
            raise ValueError('unexpected HTTP acceptance result')
        evidence.append({'method': method, 'operation': path.rsplit('/', 1)[-1],
                         'status': status, 'request_id': request_id})
        return payload

    call('GET', '/health/live')
    call('GET', '/health/ready', expected=401)
    ready = call('GET', '/health/ready', 't1_employee')
    if ready.get('mode') != 'fixture':
        raise ValueError('this verifier requires a fresh synthetic fixture; PG acceptance is separate')
    thread = thread_id
    payload = {'thread_id': thread, 'idempotency_key': thread, 'case': 'leave', 'arguments': {'days': 3}}
    result = call('POST', '/v1/threads', 't1_employee', payload)
    if not result['waiting_for_approval']:
        raise ValueError('expected pending approval')
    call('POST', '/v1/threads', 't1_employee', payload)
    call('GET', '/v1/threads/'+thread, 't2_employee', expected=403)
    call('POST', '/v1/threads/'+thread+'/decision', 't1_employee', {'approved': True}, expected=403)
    for _ in range(2):
        result = call('POST', '/v1/threads/'+thread+'/decision', 't1_manager', {'approved': True})
        if result['status'] != 'executed':
            raise ValueError('approval did not execute')
    call('GET', '/v1/threads/'+thread, 't1_employee')
    call('GET', '/v1/metrics', 't1_employee', expected=403)
    call('GET', '/v1/metrics', 't1_manager')
    return {'kind': 'm12-real-http-fixture', 'status': 'passed', 'thread_id': thread,
            'requests': evidence, 'not_verified': ['PostgreSQL', 'Ubuntu', 'GPU', 'TLS', 'SSO']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8012')
    parser.add_argument('--credentials', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        parser.error('output must not exist')
    thread_id = 'http-'+uuid4().hex[:16]
    # Emit before the first network request, even if the process is interrupted.
    print(json.dumps({'status': 'starting', 'thread_id': thread_id}), flush=True)
    try:
        credentials = json.loads(Path(args.credentials).read_text(encoding='utf-8'))
        result = verify(args.base_url, credentials, thread_id=thread_id)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps({'status': 'passed', 'requests': len(result['requests'])}))
        return 0
    except Exception:
        print('HTTP acceptance failed; inspect original thread '+thread_id+' before retry; no new key', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
