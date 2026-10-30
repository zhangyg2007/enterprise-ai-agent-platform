"""A real Uvicorn socket and a new process must preserve the approval result."""
from contextlib import closing
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from enterprise_ai.service_config import init_fixture_config
from enterprise_ai.workflow_graph import create_lab, WorkflowLab
from enterprise_ai.workflow_store import FixtureActor


class ServiceProcessTests(unittest.TestCase):
    def test_failed_verifier_preserves_recovery_thread_without_credentials(self):
        spec = importlib.util.spec_from_file_location('m12_verifier', 'scripts/m12_verify_http.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            credentials = root/'credentials.json'
            credentials.write_text('{"t1_employee":"PRIVATE"}')
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(sys, 'argv', ['verify', '--credentials', str(credentials), '--output', str(root/'out.json')]), \
                    patch.object(module, 'verify', side_effect=TimeoutError('PRIVATE')) as verify, \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(module.main(), 1)
            event = json.loads(output.getvalue().splitlines()[0])
            self.assertRegex(event['thread_id'], '^http-[a-f0-9]{16}$')
            self.assertEqual(verify.call_args.kwargs['thread_id'], event['thread_id'])
            self.assertIn(event['thread_id'], errors.getvalue())
            self.assertNotIn('PRIVATE', output.getvalue()+errors.getvalue())

    def test_http_acceptance_restart_and_no_duplicate_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            create_lab(folder/'lab')
            init_fixture_config(folder/'secrets', folder/'lab')
            credentials = json.loads((folder/'secrets/credentials.json').read_text())
            with closing(socket.socket()) as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            url = f'http://127.0.0.1:{port}'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            env = {**os.environ, 'M12_CONFIG': str(folder/'secrets/service.json')}
            command = [sys.executable, '-m', 'uvicorn', 'enterprise_ai.service_api:app_from_env',
                       '--factory', '--host', '127.0.0.1', '--port', str(port), '--workers', '1', '--no-access-log']

            def start(log):
                process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic()+20
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            with opener.open(url+'/health/live', timeout=1) as response:
                                if response.status == 200:
                                    return process
                        except (OSError, urllib.error.URLError):
                            time.sleep(.1)
                    self.fail('Uvicorn did not become healthy')
                except BaseException:
                    process.terminate()
                    process.wait(timeout=10)
                    raise

            with (folder/'server.log').open('w') as log:
                process = start(log)
                try:
                    result = subprocess.run([sys.executable, 'scripts/m12_verify_http.py', '--base-url', url,
                        '--credentials', str(folder/'secrets/credentials.json'), '--output', str(folder/'evidence.json')],
                        capture_output=True, text=True, timeout=45)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    evidence = json.loads((folder/'evidence.json').read_text())
                    self.assertEqual(len(evidence['requests']), 12)
                finally:
                    process.terminate()
                    process.wait(timeout=10)
                process = start(log)
                try:
                    request = urllib.request.Request(url+'/v1/threads/'+evidence['thread_id']+'/decision',
                        data=b'{"approved":true}', headers={'Content-Type': 'application/json',
                        'Authorization': 'Bearer '+credentials['t1_manager']})
                    with opener.open(request, timeout=10) as response:
                        self.assertEqual(json.load(response)['status'], 'executed')
                    self.assertEqual(WorkflowLab(folder/'lab').store.effect_count(FixtureActor(1,4,4,'manager')), 1)
                finally:
                    process.terminate()
                    process.wait(timeout=10)
