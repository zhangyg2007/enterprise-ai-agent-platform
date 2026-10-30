import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from enterprise_ai.workflow_graph import WorkflowLab
from enterprise_ai.workflow_store import FixtureActor


class WorkflowCLITests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'lab'

    def run_cli(self, action, *args, code=0):
        result=subprocess.run([sys.executable,'-m','enterprise_ai.workflow_cli',action,
                               '--lab',str(self.path),*args],capture_output=True,text=True,
                              encoding='utf-8',timeout=40)
        self.assertEqual(result.returncode,code,result.stdout+'\n'+result.stderr)
        return json.loads(result.stdout if code==0 else result.stderr)

    def start(self):
        self.run_cli('init')
        return self.run_cli('start','--actor','employee','--thread','leave-1',
                            '--key','key-1','--case','leave','--arguments','{"days":3}')

    def test_different_processes_pause_inspect_approve_and_replay(self):
        waiting=self.start()
        self.assertTrue(waiting['waiting_for_approval'])
        view=self.run_cli('inspect','--actor','employee','--thread','leave-1')
        self.assertEqual(view['status'],'pending')
        done=self.run_cli('approve','--actor','manager','--thread','leave-1')
        self.assertEqual(done['status'],'executed')
        repeated=self.run_cli('retry','--actor','manager','--thread','leave-1')
        self.assertEqual(done['receipt'],repeated['receipt'])

    def test_process_failure_after_commit_then_recovery_reports_replay(self):
        self.start()
        failed=self.run_cli('approve','--actor','manager','--thread','leave-1','--fail-after-effect',code=5)
        self.assertEqual(failed['error'],'synthetic_after_effect_failure')
        result=self.run_cli('retry','--actor','manager','--thread','leave-1')
        self.assertEqual(result['status'],'executed')
        self.assertTrue(result['receipt']['replayed'])
        # Opening a real business database in another process checks the count/balance.
        report=self.run_cli('inspect','--actor','manager','--thread','leave-1')
        self.assertEqual(report['effect_count'],1)
        self.assertEqual(report['resources']['leave']['remaining_days'],12)

    def test_abrupt_process_exit_releases_os_lock_and_keeps_one_effect(self):
        self.start()
        code='''import os,sys
from enterprise_ai.workflow_graph import WorkflowLab
from enterprise_ai.workflow_store import FixtureActor
lab=WorkflowLab(sys.argv[1])
execute=lab.store.execute
def stop_after_commit(actor,request_id):
    execute(actor,request_id)
    os._exit(23)
lab.store.execute=stop_after_commit
lab.decide(FixtureActor(1,4,4,'manager'),'leave-1',True)
'''
        stopped=subprocess.run([sys.executable,'-c',code,str(self.path)],capture_output=True,timeout=40)
        self.assertEqual(stopped.returncode,23,stopped.stderr)
        recovered=self.run_cli('retry','--actor','manager','--thread','leave-1')
        self.assertTrue(recovered['receipt']['replayed'])
        self.assertEqual(recovered['effect_count'],1)
        self.assertEqual(recovered['resources']['leave']['remaining_days'],12)

    def test_rejection_and_identity_boundaries_across_processes(self):
        self.start()
        self.run_cli('approve','--actor','employee','--thread','leave-1',code=3)
        self.run_cli('inspect','--actor','manager','--tenant','2','--thread','leave-1',code=3)
        result=self.run_cli('reject','--actor','manager','--thread','leave-1')
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['effect_count'],0)
        self.run_cli('approve','--actor','manager','--thread','leave-1',code=2)

    def test_os_lock_is_visible_to_another_cli_process(self):
        self.start()
        with WorkflowLab(self.path)._locked():
            result=self.run_cli('inspect','--actor','manager','--thread','leave-1',code=4)
            self.assertEqual(result['error'],'lab_busy')
        self.assertEqual(self.run_cli('inspect','--actor','employee','--thread','leave-1')['status'],'pending')

    def test_duplicate_json_and_output_overwrite_are_rejected(self):
        self.run_cli('init')
        self.run_cli('init',code=2)
        self.run_cli('start','--actor','employee','--thread','leave-1','--key','key-1',
                     '--case','leave','--arguments','{"days":3,"days":4}',code=2)
        self.assertEqual(WorkflowLab(self.path).store.effect_count(FixtureActor(1,4,4,'manager')),0)


if __name__=='__main__': unittest.main()
