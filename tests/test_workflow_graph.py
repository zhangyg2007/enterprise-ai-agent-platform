from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enterprise_ai.workflow_store import FixtureActor
from enterprise_ai.workflow_graph import WorkflowLab, WorkflowBusy, create_lab


class WorkflowGraphTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'lab'
        self.lab=create_lab(self.path)
        self.employee=FixtureActor(1,2,1,'employee')
        self.manager=FixtureActor(1,4,4,'manager')

    def start_leave(self,thread='leave-1',key='key-1',days=3):
        return self.lab.start(self.employee,thread,key,'leave',{'days':days})

    def test_actual_persistent_interrupt_reopen_and_command_approval(self):
        waiting=self.start_leave()
        self.assertTrue(waiting['waiting_for_approval'])
        self.assertEqual(waiting['status'],'pending')
        self.assertEqual(self.lab.store.effect_count(self.manager),0)
        reopened=WorkflowLab(self.path)
        self.assertTrue(reopened.inspect(self.employee,'leave-1')['waiting_for_approval'])
        done=reopened.decide(self.manager,'leave-1',True)
        self.assertEqual(done['status'],'executed')
        self.assertEqual(done['receipt']['effect']['days'],3)
        self.assertFalse(done['waiting_for_approval'])
        self.assertEqual(reopened.store.read_resource(self.employee,'leave',1)['value']['remaining_days'],12)
        self.assertEqual(reopened.retry(self.manager,'leave-1')['receipt'],done['receipt'])
        self.assertEqual(reopened.store.effect_count(self.manager),1)

    def test_rejection_is_durable_and_cannot_be_reversed(self):
        self.start_leave()
        result=WorkflowLab(self.path).decide(self.manager,'leave-1',False)
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(self.lab.store.effect_count(self.manager),0)
        with self.assertRaises(ValueError): self.lab.decide(self.manager,'leave-1',True)
        self.assertEqual(self.lab.retry(self.manager,'leave-1')['status'],'rejected')

    def test_business_commit_before_graph_failure_replays_once(self):
        self.start_leave()
        execute=self.lab.store.execute
        def fail_after_commit(actor,request_id):
            execute(actor,request_id)
            raise RuntimeError('simulated failure after business commit')
        with patch.object(self.lab.store,'execute',side_effect=fail_after_commit):
            with self.assertRaises(RuntimeError): self.lab.decide(self.manager,'leave-1',True)
        self.assertEqual(self.lab.store.effect_count(self.manager),1)
        recovered=WorkflowLab(self.path).retry(self.manager,'leave-1')
        self.assertEqual(recovered['status'],'executed')
        self.assertTrue(recovered['receipt']['replayed'])
        self.assertEqual(self.lab.store.effect_count(self.manager),1)
        self.assertEqual(self.lab.store.read_resource(self.employee,'leave',1)['value']['remaining_days'],12)

    def test_approval_commit_before_graph_failure_can_resume_without_reapproving(self):
        self.start_leave()
        decide=self.lab.store.decide
        def fail_after_decision(actor,request_id,approved):
            decide(actor,request_id,approved)
            raise RuntimeError('failure after approval commit')
        with patch.object(self.lab.store,'decide',side_effect=fail_after_decision):
            with self.assertRaises(RuntimeError): self.lab.decide(self.manager,'leave-1',True)
        self.assertEqual(self.lab.store.get_request(self.employee,'leave-1')['status'],'approved')
        result=WorkflowLab(self.path).retry(self.manager,'leave-1')
        self.assertEqual(result['status'],'executed')
        self.assertEqual(self.lab.store.effect_count(self.manager),1)

    def test_rejection_commit_before_checkpoint_failure_recovers_without_effect(self):
        self.start_leave()
        decide=self.lab.store.decide
        def fail_after_rejection(actor,request_id,approved):
            decide(actor,request_id,approved)
            raise RuntimeError('failure after rejection commit')
        with patch.object(self.lab.store,'decide',side_effect=fail_after_rejection):
            with self.assertRaises(RuntimeError): self.lab.decide(self.manager,'leave-1',False)
        self.assertFalse(self.lab.inspect(self.employee,'leave-1')['waiting_for_approval'])
        result=WorkflowLab(self.path).retry(self.manager,'leave-1')
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['next'],[])
        self.assertEqual(self.lab.store.effect_count(self.manager),0)

    def test_opposite_decision_after_approval_checkpoint_failure_is_rejected(self):
        for approved in (True,False):
            with self.subTest(approved=approved):
                thread='leave-yes' if approved else 'leave-no'
                self.start_leave(thread,'key-'+thread)
                decide=self.lab.store.decide
                def fail_after_commit(actor,request_id,value):
                    decide(actor,request_id,value)
                    raise RuntimeError('failure after immutable decision')
                with patch.object(self.lab.store,'decide',side_effect=fail_after_commit):
                    with self.assertRaises(RuntimeError): self.lab.decide(self.manager,thread,approved)
                before=self.lab.store.effect_count(self.manager)
                with self.assertRaises(ValueError): self.lab.decide(self.manager,thread,not approved)
                self.assertEqual(self.lab.store.effect_count(self.manager),before)
                final=WorkflowLab(self.path).retry(self.manager,thread)
                self.assertEqual(final['status'],'executed' if approved else 'rejected')

    def test_other_valid_lab_component_files_cannot_be_mixed(self):
        other=create_lab(self.path.parent/'other-lab')
        for name in ('business.sqlite','control.sqlite','checkpoints.sqlite'):
            with self.subTest(component=name):
                path=self.path/name
                original=path.read_bytes()
                try:
                    path.write_bytes((other.path/name).read_bytes())
                    with self.assertRaises(ValueError): WorkflowLab(self.path)
                finally:
                    path.write_bytes(original)

    def test_quote_replenishment_and_same_thread_names_are_tenant_scoped(self):
        sales=FixtureActor(1,1,1,'sales'); buyer=FixtureActor(1,3,3,'procurement')
        self.lab.start(sales,'quote-1','key-quote','quote',
                       {'customer_id':1,'product_id':1,'quantity':3,'discount_percent':'15'})
        quote=self.lab.decide(self.manager,'quote-1',True)
        self.assertEqual(quote['receipt']['effect']['total'],'255.00')
        self.lab.start(buyer,'buy-1','key-buy','replenish',{'product_id':1})
        purchase=self.lab.decide(self.manager,'buy-1',True)
        self.assertEqual(purchase['receipt']['effect']['quantity'],11)
        self.start_leave()
        other_employee=FixtureActor(2,2,1,'employee'); other_manager=FixtureActor(2,4,4,'manager')
        self.lab.start(other_employee,'leave-1','key-1','leave',{'days':4})
        self.lab.decide(other_manager,'leave-1',True)
        self.assertEqual(self.lab.inspect(self.employee,'leave-1')['status'],'pending')
        self.assertEqual(self.lab.inspect(other_employee,'leave-1')['status'],'executed')
        self.assertEqual(self.lab.store.effect_count(other_manager),1)

    def test_unauthorized_identity_is_denied_before_checkpoint_read(self):
        self.start_leave()
        with patch('enterprise_ai.workflow_graph.SqliteSaver.get_tuple',side_effect=AssertionError('checkpoint read')):
            for actor in (FixtureActor(2,4,4,'manager'),FixtureActor(1,5,5,'employee')):
                with self.assertRaises(PermissionError): self.lab.inspect(actor,'leave-1')
                with self.assertRaises(PermissionError): self.lab.decide(actor,'leave-1',True)
            with self.assertRaises(PermissionError): self.lab.decide(self.employee,'leave-1',True)
            with self.assertRaises(PermissionError): self.lab.decide(FixtureActor(1,2,1,'manager'),'leave-1',True)
            with self.assertRaises(PermissionError): self.lab.retry(self.employee,'leave-1')

    def test_start_never_overwrites_existing_checkpoint_or_key_binding(self):
        self.start_leave()
        with self.assertRaises(ValueError): self.start_leave(days=4)
        with self.assertRaises(ValueError): self.start_leave('another-thread')
        again=self.start_leave()
        self.assertTrue(again['waiting_for_approval'])
        self.lab.decide(self.manager,'leave-1',True)
        self.assertEqual(self.start_leave()['status'],'executed')
        self.assertEqual(self.lab.store.effect_count(self.manager),1)

    def test_retry_cannot_supply_or_bypass_a_missing_approval(self):
        self.start_leave()
        with self.assertRaises(ValueError): self.lab.retry(self.manager,'leave-1')
        for payload in (1,'yes',{'approved':True,'tenant_id':2}):
            with self.assertRaises(ValueError): self.lab.decide(self.manager,'leave-1',payload)
        self.assertEqual(self.lab.store.effect_count(self.manager),0)

    def test_two_approved_threads_recheck_the_same_resource_version(self):
        self.start_leave('leave-1','key-1',10)
        self.start_leave('leave-2','key-2',10)
        self.lab.decide(self.manager,'leave-1',True)
        result=self.lab.decide(self.manager,'leave-2',True)
        self.assertEqual(result['status'],'conflict')
        self.assertEqual(self.lab.store.effect_count(self.manager),1)
        self.assertEqual(self.lab.store.read_resource(self.employee,'leave',1)['value']['remaining_days'],5)

    def test_lab_lock_rejects_parallel_checkpoint_access_and_releases(self):
        self.start_leave()
        with self.lab._locked():
            with ThreadPoolExecutor(max_workers=1) as pool:
                task=pool.submit(WorkflowLab(self.path).inspect,self.manager,'leave-1')
                with self.assertRaises(WorkflowBusy): task.result()
        self.assertTrue(self.lab.inspect(self.manager,'leave-1')['waiting_for_approval'])

    def test_new_path_only_and_missing_files_do_not_create_empty_db(self):
        with self.assertRaises(FileExistsError): create_lab(self.path)
        missing=self.path.parent/'missing'
        with self.assertRaises(FileNotFoundError): WorkflowLab(missing)
        self.assertFalse(missing.exists())
        checkpoint=self.path/'checkpoints.sqlite'
        checkpoint.rename(self.path/'saved-checkpoints.sqlite')
        with self.assertRaises(FileNotFoundError): WorkflowLab(self.path)
        self.assertFalse(checkpoint.exists())


if __name__=='__main__': unittest.main()
