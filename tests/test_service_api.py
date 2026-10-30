from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from enterprise_ai.workflow_graph import create_lab
from enterprise_ai.workflow_store import FixtureActor
from enterprise_ai.service_api import create_app
from enterprise_ai.service_config import load_config, init_fixture_config


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lab = create_lab(self.root / 'lab')
        self.tokens = {name: (name + '-').ljust(48, 'x') for name in ('employee', 'manager', 'other', 'colleague', 'other_manager')}
        actors = {'employee': [1, 2, 1, 'employee'], 'manager': [1, 4, 4, 'manager'],
                  'other': [2, 2, 1, 'employee'], 'colleague': [1, 8, 8, 'employee'],
                  'other_manager': [2, 4, 4, 'manager']}
        self.config = {'mode': 'fixture', 'lab': str(self.root / 'lab'), 'principals': [
            {'token_sha256': hashlib.sha256(self.tokens[name].encode()).hexdigest(),
             'actor': dict(zip(('tenant_id', 'user_id', 'employee_id', 'role'), actor))}
            for name, actor in actors.items()]}
        self.app = create_app(self.config)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def headers(self, name='employee'):
        return {'Authorization': 'Bearer ' + self.tokens[name]}

    def start(self, **kw):
        body = {'thread_id': 'leave-1', 'idempotency_key': 'key-1', 'case': 'leave', 'arguments': {'days': 3}}
        body.update(kw)
        return self.client.post('/v1/threads', json=body, headers=self.headers())

    def test_auth_and_readiness(self):
        self.assertEqual(self.client.get('/health/live').status_code, 200)
        for path in ('/health/ready', '/v1/metrics', '/v1/threads/x'):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get('/health/ready', headers=self.headers()).status_code, 200)
        self.assertEqual(self.client.get('/v1/metrics', headers=self.headers()).status_code, 403)
        self.assertEqual(self.client.get('/v1/metrics', headers=self.headers('manager')).status_code, 200)

    def test_real_graph_approval_idempotency_and_isolation(self):
        first = self.start()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()['waiting_for_approval'])
        self.assertEqual(self.start().status_code, 200)
        for name in ('other', 'colleague'):
            self.assertEqual(self.client.get('/v1/threads/leave-1', headers=self.headers(name)).status_code, 403)
        self.assertEqual(self.client.post('/v1/threads/leave-1/decision', json={'approved': True}, headers=self.headers()).status_code, 403)
        for _ in range(2):
            response = self.client.post('/v1/threads/leave-1/decision', json={'approved': True}, headers=self.headers('manager'))
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.lab.store.effect_count(FixtureActor(1, 4, 4, 'manager')), 1)
        self.assertEqual(self.start(arguments={'days': 4}).status_code, 409)

    def test_rejection_and_retry_cannot_approve(self):
        self.start()
        self.assertEqual(self.client.post('/v1/threads/leave-1/retry', headers=self.headers('manager')).status_code, 409)
        result = self.client.post('/v1/threads/leave-1/decision', json={'approved': False}, headers=self.headers('manager'))
        self.assertEqual(result.json()['status'], 'rejected')
        self.assertEqual(self.lab.store.effect_count(FixtureActor(1, 4, 4, 'manager')), 0)

    def test_strict_input_and_no_echo(self):
        for body in ({'tenant_id': 2}, {'actor': 'manager'}, {'arguments': {'days': True}},
                     {'arguments': {'days': 1, 'role': 'manager'}}):
            self.assertIn(self.start(**body).status_code, (409, 422))
        result = self.client.post('/v1/threads/leave-1/decision', json={'approved': 'true', 'token': 'PRIVATE'}, headers=self.headers())
        self.assertEqual(result.status_code, 422)
        self.assertNotIn('PRIVATE', result.text)
        self.assertEqual(self.client.post('/v1/threads', content=b'x'*8193, headers=self.headers()).status_code, 413)

    def test_observability_redacts_input(self):
        with self.assertLogs('enterprise_ai.http', level='INFO') as logs:
            result = self.client.get('/v1/threads/PRIVATE?token=SECRET', headers=self.headers())
        self.assertEqual(result.status_code, 403)
        self.assertRegex(result.headers['x-request-id'], '^[a-f0-9]{32}$')
        for secret in ('PRIVATE', 'SECRET', self.tokens['employee']):
            self.assertNotIn(secret, '\n'.join(logs.output))

    def test_business_audit_links_to_http_for_start_deny_approve_and_retry(self):
        # Ledger request_id equals the thread ID; hash joins without logging raw input.
        expected = hashlib.sha256(b'1:leave-1').hexdigest()
        with self.assertLogs('enterprise_ai.http', level='INFO') as logs:
            responses = [self.start(),
                self.client.post('/v1/threads/leave-1/decision', json={'approved': True}, headers=self.headers()),
                self.client.post('/v1/threads/leave-1/decision', json={'approved': True}, headers=self.headers('manager')),
                self.client.post('/v1/threads/leave-1/decision', json={'approved': True}, headers=self.headers('manager'))]
        self.assertEqual([r.status_code for r in responses], [200, 403, 200, 200])
        entries = [json.loads(item.split(':', 2)[2]) for item in logs.output]
        for response, entry in zip(responses, entries):
            self.assertEqual(entry['request_id'], response.headers['x-request-id'])
            self.assertEqual(entry['thread_digest'], expected)
            self.assertEqual(entry['actor']['tenant_id'], 1)
            self.assertNotIn('leave-1', json.dumps(entry))
        self.assertEqual(self.lab.store.effect_count(FixtureActor(1, 4, 4, 'manager')), 1)

    def test_manager_metrics_exclude_other_tenant_and_unauthenticated_requests(self):
        before = self.client.get('/v1/metrics', headers=self.headers('manager')).json()['requests']
        for _ in range(3):
            self.assertEqual(self.client.get('/health/ready', headers=self.headers('other')).status_code, 200)
        self.client.get('/health/ready')
        after = self.client.get('/v1/metrics', headers=self.headers('manager')).json()
        other = self.client.get('/v1/metrics', headers=self.headers('other_manager')).json()
        self.assertEqual(after['requests'], before + 1)  # previous metrics request only
        self.assertGreaterEqual(other['requests'], 3)
        self.assertEqual(after['scope'], 'tenant_process_only')

    def test_unavailable_and_busy_are_sanitized(self):
        from enterprise_ai.workflow_graph import WorkflowBusy
        for error, status in ((WorkflowBusy('SECRET'), 409), (RuntimeError('SECRET'), 503)):
            with patch('enterprise_ai.workflow_graph.WorkflowLab.inspect', side_effect=error):
                response = self.client.get('/v1/threads/any', headers=self.headers())
            self.assertEqual(response.status_code, status)
            self.assertNotIn('SECRET', response.text)

    def test_rate_limit(self):
        with TestClient(create_app(self.config, requests_per_minute=2)) as client:
            for _ in range(2):
                self.assertEqual(client.get('/health/ready', headers=self.headers()).status_code, 200)
            result = client.get('/health/ready', headers=self.headers())
            self.assertEqual(result.status_code, 429)
            self.assertEqual(result.headers['retry-after'], '60')

    def test_private_config_generation_and_no_overwrite(self):
        target = self.root / 'secrets'
        init_fixture_config(target, self.root / 'lab')
        config = load_config(target / 'service.json')
        self.assertEqual(len(config['principals']), 8)
        credentials = json.loads((target / 'credentials.json').read_text())
        self.assertEqual(len(set(credentials.values())), 8)
        with self.assertRaises(FileExistsError):
            init_fixture_config(target, self.root / 'lab')
        with TestClient(create_app(config)) as client:
            self.assertEqual(client.get('/health/ready', headers={'Authorization': 'Bearer '+credentials['t1_employee']}).status_code, 200)

    def test_bad_configuration_fails_closed(self):
        for value in ({}, {**self.config, 'mode': 'unknown'}, {**self.config, 'principals': []},
                      {**self.config, 'principals': self.config['principals']*2}):
            with self.assertRaises(ValueError):
                create_app(value)

    def test_pg_uses_only_server_service_mapping(self):
        pg = {'mode': 'postgres', 'checkpoint_service': 'm11_cp', 'principals': [
            {'token_sha256': self.config['principals'][0]['token_sha256'], 'service': 'm11_t1_employee'}]}
        with patch('enterprise_ai.workflow_pg.PgWorkflowLab') as cls:
            cls.return_value.store.actor = FixtureActor(1, 2, 1, 'employee')
            cls.return_value.inspect.return_value = {'status': 'pending'}
            with TestClient(create_app(pg)) as client:
                response = client.get('/v1/threads/a', headers=self.headers())
            self.assertEqual(response.status_code, 200)
            cls.assert_called_once_with('m11_t1_employee', 'm11_cp')
            cls.return_value.inspect.assert_called_once_with(FixtureActor(1, 2, 1, 'employee'), 'a')
