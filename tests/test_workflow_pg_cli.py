import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
import subprocess
import sys
from enterprise_ai.workflow_pg_cli import main


class PgCliTests(unittest.TestCase):
    def test_opt_in_diagnostic_file_is_private_exclusive_and_not_console_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'diagnostic.txt'; stderr=io.StringIO()
            with patch('enterprise_ai.workflow_pg_cli.PgWorkflowLab',side_effect=RuntimeError('private detail')), \
                 contextlib.redirect_stderr(stderr):
                self.assertEqual(main(['--diagnostic-log',str(log),'inspect','--service','employee','--thread','x']),1)
            self.assertIn('RuntimeError: private detail',log.read_text())
            self.assertNotIn('private detail',stderr.getvalue())
            if os.name!='nt': self.assertEqual(log.stat().st_mode & 0o777,0o600)
            before=log.read_bytes()
            with patch('enterprise_ai.workflow_pg_cli.PgWorkflowLab',side_effect=RuntimeError('new detail')), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--diagnostic-log',str(log),'inspect','--service','employee','--thread','x']),1)
            self.assertEqual(log.read_bytes(),before)

    def test_server_verifier_invalid_run_preserves_diagnostic_without_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'server-diagnostic.txt'
            result=subprocess.run([sys.executable,'scripts/m11_verify_pg.py','--run','invalid!',
                                   '--diagnostic-log',str(log)],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,1)
            self.assertIn('ValueError',log.read_text())
            self.assertNotIn('Traceback',result.stderr)

    def test_services_are_exclusive_private_and_secrets_not_printed(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()) as output:
            folder=Path(tmp)/'private'
            self.assertEqual(main(['create-services','--directory',str(folder),'--port','5432']),0)
            self.assertIn('[m11-a-employee]',(folder/'services.conf').read_text())
            password=(folder/'passwords.sql').read_text()
            self.assertIn('ALTER ROLE m11_a_employee PASSWORD',password)
            self.assertNotIn('PASSWORD',output.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['create-services','--directory',str(folder),'--port','5432']),2)

    def test_demo_snapshot_is_new_file_only_and_normalizes(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            file=str(Path(tmp)/'snapshot.json')
            self.assertEqual(main(['demo-snapshot','--output',file]),0)
            from enterprise_ai.workflow_snapshot import normalize_snapshot
            self.assertEqual(normalize_snapshot(json.loads(Path(file).read_text()))['tenant'],1)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['demo-snapshot','--output',file]),2)

    def test_cli_cannot_accept_self_reported_actor_or_tenant(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            main(['inspect','--service','employee','--thread','x','--tenant','2'])

    def test_permission_error_is_sanitized_and_exit_3(self):
        output=io.StringIO()
        with patch('enterprise_ai.workflow_pg_cli.PgWorkflowLab',side_effect=PermissionError('private password')), \
             contextlib.redirect_stderr(output):
            self.assertEqual(main(['inspect','--service','employee','--thread','x']),3)
        self.assertNotIn('password',output.getvalue())


if __name__=='__main__': unittest.main()
