import copy
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from enterprise_ai.business_agent import prepare, decide
from enterprise_ai.guarded_workflow import create_guarded_lab
from enterprise_ai.service_api import create_app
from enterprise_ai.workflow_graph import WorkflowLab, create_lab
from enterprise_ai.workflow_backup import backup_lab, restore_lab
from enterprise_ai.workflow_store import FixtureActor
import tests.test_business_agent as business_fixtures


class GuardedWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        business_fixtures.BusinessAgentTests.setUpClass()
        cls.index, cls.state, cls.encoder = business_fixtures.BusinessAgentTests.index, business_fixtures.BusinessAgentTests.state, business_fixtures.BusinessAgentTests.encoder

    @classmethod
    def tearDownClass(cls): business_fixtures.BusinessAgentTests.tearDownClass()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'guarded'
        self.lab = create_guarded_lab(self.path)
        self.actor = FixtureActor(1,2,1,'employee')
        self.manager = FixtureActor(1,4,4,'manager')

    def proposal(self):
        return prepare(self.lab, self.actor, 'guarded-leave', 'guarded-key', '申请3天',
                       lambda _: {'case':'leave', 'arguments':{'days':3}},
                       self.index, self.state, self.encoder, as_of='2026-10-22')

    def test_old_m11_start_decide_and_retry_are_blocked_across_reopen(self):
        with self.assertRaises(PermissionError):
            self.lab.start(self.actor, 'unguarded', 'key', 'leave', {'days':3})
        ctx = self.proposal()
        self.assertTrue(ctx['workflow']['waiting_for_approval'])
        reopened = WorkflowLab(self.path)
        with self.assertRaises(PermissionError): reopened.decide(self.manager, 'guarded-leave', True)
        with self.assertRaises(PermissionError): reopened.retry(self.manager, 'guarded-leave')
        self.assertEqual(reopened.store.effect_count(self.manager), 0)
        result = decide(reopened, self.manager, ctx, True, self.index, self.state, self.encoder)
        self.assertEqual(result['status'], 'executed')
        self.assertEqual(reopened.store.effect_count(self.manager), 1)

    def test_proposal_binding_is_immutable_and_refuses_modified_context(self):
        ctx = self.proposal()
        for field, value in [('as_of','2026-10-21'), ('index_digest','0'*64), ('citations_sha256','0'*64)]:
            tampered = copy.deepcopy(ctx)
            tampered[field] = value
            with self.assertRaises(ValueError):
                decide(self.lab, self.manager, tampered, True, self.index, self.state, self.encoder)
        self.assertEqual(self.lab.store.effect_count(self.manager), 0)
        retry = self.proposal()
        self.assertEqual(retry['thread_id'], ctx['thread_id'])

    def test_existing_http_routes_cannot_bypass_guarded_lab(self):
        token = 'synthetic-employee-'.ljust(48,'x')
        manager_token = 'synthetic-manager-'.ljust(48,'x')
        principals = []
        for secret, actor in ((token,self.actor),(manager_token,self.manager)):
            principals.append({'token_sha256':hashlib.sha256(secret.encode()).hexdigest(),
                'actor':{'tenant_id':actor.tenant_id,'user_id':actor.user_id,
                         'employee_id':actor.employee_id,'role':actor.role}})
        client = TestClient(create_app({'mode':'fixture','lab':str(self.path),'principals':principals}))
        self.addCleanup(client.close)
        body={'thread_id':'bad','idempotency_key':'bad-key','case':'leave','arguments':{'days':3}}
        self.assertEqual(client.post('/v1/threads',json=body,headers={'Authorization':'Bearer '+token}).status_code,403)
        self.proposal()
        response=client.post('/v1/threads/guarded-leave/decision',json={'approved':True},
                             headers={'Authorization':'Bearer '+manager_token})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.lab.store.effect_count(self.manager),0)

    def test_policy_wrapper_refuses_legacy_lab(self):
        legacy = create_lab(Path(self.tmp.name)/'legacy')
        with self.assertRaises(ValueError):
            prepare(legacy, self.actor, 'legacy-id', 'legacy-key', '申请3天',
                    lambda _: {'case':'leave','arguments':{'days':3}},
                    self.index, self.state, self.encoder, as_of='2026-10-22')
        self.assertEqual(legacy.store.effect_count(self.manager), 0)

    def test_backup_restore_preserves_guarded_binding_and_approval(self):
        ctx = self.proposal()
        backup = Path(self.tmp.name)/'backup'
        restored = Path(self.tmp.name)/'restored'
        manifest = backup_lab(self.path, backup)
        self.assertEqual(manifest['lab_format'], 2)
        restore_lab(backup, restored)
        copy_lab = WorkflowLab(restored)
        with self.assertRaises(PermissionError):
            copy_lab.decide(self.manager, 'guarded-leave', True)
        result = decide(copy_lab, self.manager, ctx, True, self.index, self.state, self.encoder)
        self.assertEqual(result['status'], 'executed')
        self.assertEqual(copy_lab.store.effect_count(self.manager), 1)
        self.assertEqual(self.lab.store.effect_count(self.manager), 0)

    def test_legacy_cli_process_cannot_start_or_approve_protected_requests(self):
        self.proposal()
        command = [sys.executable, '-m', 'enterprise_ai.workflow_cli', 'approve', '--lab', str(self.path),
                   '--actor', 'manager', '--thread', 'guarded-leave']
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, env=os.environ.copy())
        self.assertEqual(result.returncode, 3, result.stdout+result.stderr)
        self.assertIn('permission_denied', result.stderr)
        self.assertEqual(self.lab.store.effect_count(self.manager), 0)

    def test_interrupted_registration_can_resume_with_the_same_binding(self):
        with patch.object(self.lab, 'start', side_effect=RuntimeError('synthetic stop')):
            with self.assertRaises(RuntimeError): self.proposal()
        ctx = self.proposal()
        self.assertTrue(ctx['workflow']['waiting_for_approval'])
        result = decide(self.lab, self.manager, ctx, True, self.index, self.state, self.encoder)
        self.assertEqual(result['status'], 'executed')
        self.assertEqual(self.lab.store.effect_count(self.manager), 1)

    def test_incomplete_guarded_initialization_fails_closed(self):
        with closing(sqlite3.connect(self.path/'business.sqlite')) as db:
            db.execute('UPDATE m11_lab_metadata SET format_version=1 WHERE slot=1')
            db.commit()
        with self.assertRaises(ValueError): WorkflowLab(self.path)
