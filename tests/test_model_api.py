import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from enterprise_ai import model_api
from enterprise_ai.baseline import SYSTEM_PROMPT


KEY = 'synthetic-test-key-not-a-real-credential'
ANSWER = {'intent': 'leave_balance', 'customer_id': None, 'product_id': None, 'action': 'query'}


def completion():
    return {'model': 'enterprise-qwen', 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': json.dumps(ANSWER)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}


class Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, size):
        return self.body[:size]


class Connection:
    responses = []
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.responses.pop(0)

    def close(self):
        pass


class ModelApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / 'probe.json'
        Connection.requests = []
        Connection.responses = [Response(401, {'detail': 'denied'}),
            Response(200, {'data': [{'id': 'enterprise-qwen'}]}), Response(200, completion())]
        self.network = patch.object(model_api.http.client, 'HTTPConnection', Connection)
        self.network.start()
        self.addCleanup(self.network.stop)

    def probe(self):
        return model_api.probe('http://127.0.0.1:8000', 'enterprise-qwen', self.output, api_key=KEY)

    def test_authenticated_contract_probe_and_no_secret_in_report(self):
        report = self.probe()
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(report['unauthenticated_rejected'])
        self.assertNotIn(KEY, self.output.read_text())
        self.assertNotIn('Authorization', Connection.requests[0][3])
        self.assertEqual(Connection.requests[1][3]['Authorization'], 'Bearer ' + KEY)
        payload = json.loads(Connection.requests[2][2])
        self.assertEqual(payload['messages'][0]['content'], SYSTEM_PROMPT)
        self.assertFalse(payload['stream'])

    def test_no_authentication_is_failure(self):
        Connection.responses[0] = Response(200, {'data': []})
        self.assertEqual(self.probe()['status'], 'failed')
        self.assertEqual(len(Connection.requests), 1)

    def test_wrong_alias_stops_before_chat(self):
        Connection.responses[1] = Response(200, {'data': [{'id': 'wrong'}]})
        self.assertEqual(self.probe()['error'], 'model_alias')
        self.assertEqual(len(Connection.requests), 2)

    def test_length_and_wrong_answer_not_contract_success(self):
        value = completion()
        value['choices'][0]['finish_reason'] = 'length'
        Connection.responses[2] = Response(200, value)
        report = self.probe()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['score']['failure'], 'truncated')

    def test_invalid_success_payloads_are_not_healthy(self):
        for value in ({'error': {'message': KEY}}, {'choices': []},
                      dict(completion(), usage={'prompt_tokens': True, 'completion_tokens': 1, 'total_tokens': 2})):
            with self.subTest(value=value):
                self.output = Path(self.tmp.name) / ('report-' + str(len(list(Path(self.tmp.name).iterdir()))) + '.json')
                Connection.responses = [Response(401, {}), Response(200, {'data': [{'id': 'enterprise-qwen'}]}), Response(200, value)]
                self.assertEqual(self.probe()['status'], 'failed')
                self.assertNotIn(KEY, self.output.read_text())

    def test_remote_url_or_missing_key_rejected_before_network(self):
        for url, key in [('https://example.com', KEY), ('http://127.0.0.1:8000', ''),
                         ('http://localhost@evil.example', KEY), ('http://127.0.0.1:8000', 'bad\nheader')]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                model_api.probe(url, 'enterprise-qwen', self.output, api_key=key)
        self.assertEqual(Connection.requests, [])

    def test_existing_report_is_not_overwritten(self):
        self.output.write_text('KEEP')
        with self.assertRaises(FileExistsError):
            self.probe()
        self.assertEqual(Connection.requests, [])
        self.assertEqual(self.output.read_text(), 'KEEP')

    def test_redirect_and_oversize_do_not_follow_or_retry(self):
        for response in (Response(302, {}), Response(200, b'x' * (4 * 1024 * 1024 + 1))):
            self.output = Path(self.tmp.name) / ('report-' + str(len(list(Path(self.tmp.name).iterdir()))) + '.json')
            Connection.responses = [Response(401, {}), response]
            Connection.requests = []
            self.assertEqual(self.probe()['status'], 'failed')
            self.assertEqual(len(Connection.requests), 2)

    def test_cli_reads_key_from_environment_and_failures_return_nonzero(self):
        Connection.responses[0] = Response(200, {})
        with patch.dict(model_api.os.environ, {'API_KEY': KEY}), contextlib.redirect_stdout(io.StringIO()):
            code = model_api.main(['probe', '--model', 'enterprise-qwen', '--output', str(self.output)])
        self.assertNotEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
