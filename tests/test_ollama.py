"""Exercise the real client against an in-memory HTTP boundary, never a server."""
import io
import json
import socket
import unittest
from unittest.mock import patch

from enterprise_ai.ollama import ModelError, OllamaClient, strict_json_loads


MODEL = 'local-model:27b'


def reply(**changes):
    value = {'model': MODEL, 'created_at': '2026-10-17T00:00:00Z',
             'message': {'role': 'assistant', 'content': '{}'},
             'done': True, 'done_reason': 'stop', 'eval_count': 2,
             'eval_duration': 1000000000}
    value.update(changes)
    return value


class Response:
    def __init__(self, body, status=200):
        self.status = status
        self.stream = io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())

    def read(self, size):
        return self.stream.read(size)


class Boundary:
    """Only replaces network I/O; all serialization and protocol validation stay real."""
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.connections = []
        self.closed = 0

    def connect(self, host, port=None, timeout=None):
        self.connections.append((host, port, timeout))
        boundary = self

        class Connection:
            def request(self, method, path, body=None, headers=None):
                boundary.requests.append((method, path, body, headers))

            def getresponse(self):
                response = boundary.responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response

            def close(self):
                boundary.closed += 1

        return Connection()


class OllamaTests(unittest.TestCase):
    def boundary(self, *responses):
        boundary = Boundary(*responses)
        patcher = patch('enterprise_ai.ollama.http.client.HTTPConnection', boundary.connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        return boundary

    def chat(self, client=None, **kwargs):
        return (client or OllamaClient()).chat(
            MODEL, [{'role': 'user', 'content': '查询客户 12'}],
            format_schema={'type': 'object'}, options={'temperature': 0, 'seed': 42}, **kwargs)

    def test_strict_json_rejects_ambiguous_and_nonfinite_numbers(self):
        for text in ('{"a":1,"a":2}', '{"x":{"a":1,"a":2}}',
                     '{"a":NaN}', '[Infinity]', '[-Infinity]', '[1e999]', 'oops'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                strict_json_loads(text)
        self.assertEqual(strict_json_loads('{"a":[1,null,true,0.5]}'), {'a': [1, None, True, 0.5]})

    def test_rejects_remote_ambiguous_or_credentialed_endpoints(self):
        for url in ('https://127.0.0.1:11434', 'http://example.com', 'http://127.0.0.2',
                    'http://127.1', 'http://2130706433', 'http://127.0.0.1.evil',
                    'http://user:pass@127.0.0.1', 'http://127.0.0.1/api',
                    'http://127.0.0.1?x=1', 'http://127.0.0.1#x',
                    ' http://127.0.0.1', 'http://127.0.0.1:0', 'http://127.0.0.1:65536'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OllamaClient(url)

    def test_timeout_must_be_positive_finite_number(self):
        for value in (0, -1, True, float('nan'), float('inf'), '120'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                OllamaClient(timeout_seconds=value)

    def test_base_url_records_the_normalized_destination(self):
        self.assertEqual(OllamaClient().base_url, 'http://127.0.0.1:11434')
        self.assertEqual(OllamaClient('http://localhost:11435/').base_url,
                         'http://127.0.0.1:11435')
        self.assertEqual(OllamaClient('http://[::1]').base_url, 'http://[::1]:80')

    def test_chat_request_is_nonstreaming_and_thinking_auto_is_omitted(self):
        boundary = self.boundary(Response(reply()))
        self.assertEqual(self.chat()['message']['content'], '{}')
        method, path, body, headers = boundary.requests[0]
        self.assertEqual((method, path), ('POST', '/api/chat'))
        payload = json.loads(body)
        self.assertEqual(payload, {
            'model': MODEL, 'messages': [{'role': 'user', 'content': '查询客户 12'}],
            'format': {'type': 'object'}, 'options': {'temperature': 0, 'seed': 42},
            'stream': False, 'keep_alive': '5m'})
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(boundary.connections, [('127.0.0.1', 11434, 120)])
        self.assertEqual(boundary.closed, 1)

    def test_localhost_is_mapped_to_literal_and_ipv6_is_supported(self):
        boundary = self.boundary(Response(reply()), Response(reply()))
        self.chat(OllamaClient('http://localhost:11435/', 30), think=False)
        self.chat(OllamaClient('http://[::1]:11436', 45), think=True)
        self.assertEqual(boundary.connections, [('127.0.0.1', 11435, 30), ('::1', 11436, 45)])
        self.assertIs(json.loads(boundary.requests[0][2])['think'], False)
        self.assertIs(json.loads(boundary.requests[1][2])['think'], True)

    def test_http_errors_are_safe_and_are_not_retried_or_redirected(self):
        for status in (301, 400, 404, 429, 500, 502):
            boundary = self.boundary(Response({'error': 'private-server-secret'}, status))
            with self.subTest(status=status), self.assertRaises(ModelError) as caught:
                self.chat()
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn('private-server-secret', str(caught.exception))
            self.assertEqual(len(boundary.requests), 1)
            self.assertEqual(boundary.closed, 1)

    def test_network_timeout_and_connection_failure_are_distinct(self):
        for exception, code in ((socket.timeout('secret'), 'timeout'),
                                (ConnectionRefusedError('secret'), 'connection')):
            boundary = self.boundary(exception)
            with self.assertRaises(ModelError) as caught:
                self.chat()
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn('secret', str(caught.exception))
            self.assertEqual(boundary.closed, 1)

    def test_response_larger_than_four_mib_is_rejected(self):
        self.boundary(Response(b'x' * (4 * 1024 * 1024 + 1)))
        with self.assertRaises(ModelError) as caught:
            self.chat()
        self.assertEqual(caught.exception.code, 'response_too_large')

    def test_malformed_envelopes_are_protocol_errors_and_preserve_parsed_raw(self):
        invalid = [b'not-json', b'\xff', b'{"done":true,"done":false}', [],
                   reply(done=False), reply(done=1), reply(model='another:tag'),
                   reply(message={'role': 'user', 'content': '{}'}),
                   reply(message={'role': 'assistant', 'content': 123}),
                   reply(message={'role': 'assistant', 'content': '{}', 'tool_calls': [{'function': {}}]})]
        missing_model = reply()
        del missing_model['model']
        invalid.append(missing_model)
        for value in invalid:
            self.boundary(Response(value))
            with self.subTest(value=value), self.assertRaises(ModelError) as caught:
                self.chat()
            self.assertEqual(caught.exception.code, 'protocol')
            if isinstance(value, dict):
                self.assertEqual(caught.exception.raw, value)

    def test_error_in_success_status_is_not_treated_as_answer(self):
        self.boundary(Response({'error': 'sensitive internal text'}))
        with self.assertRaises(ModelError) as caught:
            self.chat()
        self.assertEqual(caught.exception.code, 'server_error')
        self.assertNotIn('sensitive', str(caught.exception))

    def test_chat_rejects_remote_metadata_even_without_inventory(self):
        self.boundary(Response(reply(remote_host='https://ollama.com')))
        with self.assertRaises(ModelError) as caught:
            self.chat()
        self.assertEqual(caught.exception.code, 'remote_model')

    def test_empty_or_truncated_content_is_returned_for_scoring(self):
        expected = reply(message={'role': 'assistant', 'content': '', 'thinking': 'partial'},
                         done_reason='length')
        self.boundary(Response(expected))
        self.assertEqual(self.chat(), expected)

    def inventory_boundary(self, *, tag=None, show=None, version=None):
        return self.boundary(Response(version if version is not None else {'version': '0.99-test'}),
                             Response({'models': [tag if tag is not None else {
                                 'name': MODEL, 'model': MODEL, 'digest': 'sha256:abc',
                                 'details': {'format': 'gguf'}}]}),
                             Response(show if show is not None else {
                                 'details': {'format': 'gguf', 'quantization_level': 'Q4_K_M'},
                                 'capabilities': ['completion'], 'template': 'test-template',
                                 'parameters': 'temperature 0'}))

    def test_inventory_records_exact_identity_without_guessing_missing_metadata(self):
        boundary = self.inventory_boundary(show={'details': {'format': 'gguf'}})
        result = OllamaClient().inventory(MODEL)
        self.assertEqual(result, {'model': MODEL, 'digest': 'sha256:abc',
                                  'server_version': '0.99-test', 'details': {'format': 'gguf'},
                                  'capabilities': None, 'template_sha256': None,
                                  'show': {'details': {'format': 'gguf'}}})
        self.assertEqual([(r[0], r[1]) for r in boundary.requests],
                         [('GET', '/api/version'), ('GET', '/api/tags'), ('POST', '/api/show')])
        self.assertEqual(json.loads(boundary.requests[-1][2]), {'model': MODEL})

    def test_inventory_hashes_template_and_retains_show(self):
        self.inventory_boundary()
        result = OllamaClient().inventory(MODEL)
        self.assertEqual(result['template_sha256'],
                         '99cea97def9d83bd573567f4b9b166f872d95e44cb560e44e4b85cb7327b91bc')
        self.assertEqual(result['show']['parameters'], 'temperature 0')

    def test_inventory_rejects_unmatched_or_missing_digest(self):
        for tag, code in (({'name': 'local-model:latest', 'digest': 'abc'}, 'model_not_found'),
                          ({'name': MODEL, 'digest': ''}, 'protocol'),
                          ({'name': MODEL}, 'protocol')):
            self.inventory_boundary(tag=tag)
            with self.subTest(tag=tag), self.assertRaises(ModelError) as caught:
                OllamaClient().inventory(MODEL)
            self.assertEqual(caught.exception.code, code)

    def test_inventory_rejects_known_remote_markers(self):
        for tag, show in (({'name': MODEL, 'digest': 'abc', 'remote_model': 'remote'}, {}),
                          ({'name': MODEL, 'digest': 'abc'}, {'remote_host': 'https://ollama.com'})):
            self.inventory_boundary(tag=tag, show=show)
            with self.assertRaises(ModelError) as caught:
                OllamaClient().inventory(MODEL)
            self.assertEqual(caught.exception.code, 'remote_model')

    def test_inventory_rejects_malformed_version(self):
        self.inventory_boundary(version={'version': ''})
        with self.assertRaises(ModelError) as caught:
            OllamaClient().inventory(MODEL)
        self.assertEqual(caught.exception.code, 'protocol')

    def test_cloud_tag_is_rejected_before_any_request(self):
        boundary = self.boundary()
        with self.assertRaises(ModelError) as caught:
            OllamaClient().inventory('model:cloud')
        self.assertEqual(caught.exception.code, 'remote_model')
        self.assertEqual(boundary.requests, [])


if __name__ == '__main__':
    unittest.main()
