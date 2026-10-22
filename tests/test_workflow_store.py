import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path

from enterprise_ai.workflow_store import FixtureActor, FixtureStore, create_store


class WorkflowStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'business.sqlite'
        self.store=create_store(self.path)
        self.sales=FixtureActor(1,1,1,'sales')
        self.employee=FixtureActor(1,2,1,'employee')
        self.procurement=FixtureActor(1,3,3,'procurement')
        self.manager=FixtureActor(1,4,4,'manager')

    def quote(self,request='quote-1',key='key-quote',**changes):
        args={'customer_id':1,'product_id':1,'quantity':3,'discount_percent':'15.00'}
        args.update(changes)
        return self.store.draft(self.sales,request,key,'quote',args)

    def test_quote_hand_calculated_and_two_quotes_do_not_consume_price(self):
        first=self.quote()
        self.assertEqual(first['plan']['total'],'255.00')
        self.quote('quote-2','key-quote-2')
        for rid in ('quote-1','quote-2'):
            self.store.decide(self.manager,rid,True)
            r=self.store.execute(self.manager,rid)
            self.assertEqual(r['status'],'executed'); self.assertEqual(r['effect']['total'],'255.00')
        self.assertEqual(self.store.read_resource(self.sales,'catalog',1)['revision'],1)
        self.assertEqual(self.store.effect_count(self.manager),2)

    def test_leave_and_replenishment_business_effects_are_distinct(self):
        self.store.draft(self.employee,'leave-1','key-leave','leave',{'days':3})
        self.store.decide(self.manager,'leave-1',True)
        self.store.execute(self.manager,'leave-1')
        self.assertEqual(self.store.read_resource(self.employee,'leave',1)['value']['remaining_days'],12)
        draft=self.store.draft(self.procurement,'buy-1','key-buy','replenish',{'product_id':1})
        self.assertEqual(draft['plan']['quantity'],11)
        self.store.decide(self.manager,'buy-1',True); self.store.execute(self.manager,'buy-1')
        stock=self.store.read_resource(self.procurement,'stock',1)['value']
        self.assertEqual(stock['on_order'],15); self.assertEqual(stock['on_hand'],20)

    def test_rejection_and_pending_never_write_business_effect(self):
        self.quote()
        with self.assertRaises(ValueError): self.store.execute(self.manager,'quote-1')
        self.store.decide(self.manager,'quote-1',False)
        with self.assertRaises(ValueError): self.store.execute(self.manager,'quote-1')
        with self.assertRaises(ValueError): self.store.decide(self.manager,'quote-1',True)
        self.assertEqual(self.store.effect_count(self.manager),0)

    def test_key_binding_and_restart_replay_return_original_receipt(self):
        self.quote()
        same=self.quote('different-id')
        self.assertEqual(same['request_id'],'quote-1')
        with self.assertRaises(ValueError): self.quote('different-id',quantity=4)
        self.store.decide(self.manager,'quote-1',True)
        receipt=self.store.execute(self.manager,'quote-1')
        reopened=FixtureStore(self.path)
        repeated=reopened.execute(self.manager,'quote-1')
        self.assertEqual(receipt['effect'],repeated['effect'])
        self.assertTrue(repeated['replayed']); self.assertEqual(reopened.effect_count(self.manager),1)

    def test_resource_revision_conflict_does_not_double_consume_balance(self):
        for rid,key in [('leave-1','key-1'),('leave-2','key-2')]:
            self.store.draft(self.employee,rid,key,'leave',{'days':10})
            self.store.decide(self.manager,rid,True)
        self.store.execute(self.manager,'leave-1')
        result=self.store.execute(self.manager,'leave-2')
        self.assertEqual(result['status'],'conflict')
        self.assertEqual(self.store.read_resource(self.employee,'leave',1)['value']['remaining_days'],5)
        self.assertEqual(self.store.effect_count(self.manager),1)

    def test_owner_tenant_manager_and_self_approval_checks(self):
        self.quote()
        for actor in (FixtureActor(2,4,4,'manager'),FixtureActor(1,5,5,'sales')):
            with self.assertRaises(PermissionError): self.store.get_request(actor,'quote-1')
            with self.assertRaises(PermissionError): self.store.decide(actor,'quote-1',True)
        with self.assertRaises(PermissionError): self.store.decide(self.sales,'quote-1',True)
        with self.assertRaises(PermissionError): self.store.decide(FixtureActor(1,1,1,'manager'),'quote-1',True)
        with self.assertRaises(PermissionError): self.store.read_resource(self.employee,'leave',2)
        with self.assertRaises(PermissionError):
            self.store.draft(FixtureActor(1,5,5,'sales'),'new','key-quote','quote',{'customer_id':1,'product_id':1,'quantity':3,'discount_percent':'15.00'})

    def test_invalid_inputs_and_existing_database_protected(self):
        for values in ({'quantity':True},{'quantity':0},{'discount_percent':'NaN'},
                       {'discount_percent':15.0},{'discount_percent':'31.00'}, {'tenant_id':2}):
            with self.assertRaises(ValueError): self.quote(**values)
        with self.assertRaises(ValueError):
            self.store.draft(self.employee,'leave-1','key-1','leave',{'days':1,'employee_id':2})
        with self.assertRaises(FileExistsError): create_store(self.path)
        with self.assertRaises(FileNotFoundError): FixtureStore(self.path.parent/'missing.sqlite')
        with self.assertRaises(ValueError): FixtureActor(True,1,1,'manager')

    def test_transaction_failure_rolls_back_resource_receipt_and_status(self):
        self.store.draft(self.employee,'leave-1','key-1','leave',{'days':3})
        self.store.decide(self.manager,'leave-1',True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER reject_effect BEFORE INSERT ON effects BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
            db.commit()
        with self.assertRaises(sqlite3.IntegrityError): self.store.execute(self.manager,'leave-1')
        self.assertEqual(self.store.read_resource(self.employee,'leave',1)['value']['remaining_days'],15)
        self.assertEqual(self.store.get_request(self.employee,'leave-1')['status'],'approved')
        self.assertEqual(self.store.effect_count(self.manager),0)

    def test_concurrent_execution_creates_only_one_effect(self):
        self.store.draft(self.employee,'leave-1','key-1','leave',{'days':3})
        self.store.decide(self.manager,'leave-1',True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _: FixtureStore(self.path).execute(self.manager,'leave-1'), range(2)))
        self.assertEqual(sorted(r['replayed'] for r in results),[False,True])
        self.assertEqual(self.store.effect_count(self.manager),1)
        self.assertEqual(self.store.read_resource(self.employee,'leave',1)['value']['remaining_days'],12)
        # Replay must return the receipt even though its own write advanced the revision.
        self.assertTrue(FixtureStore(self.path).execute(self.manager,'leave-1')['replayed'])

    def test_tenants_have_independent_keys_and_replay_remains_authorized(self):
        self.quote()
        other_sales=FixtureActor(2,1,1,'sales'); other_manager=FixtureActor(2,4,4,'manager')
        self.store.draft(other_sales,'quote-1','key-quote','quote',
                         {'customer_id':1,'product_id':1,'quantity':1,'discount_percent':'0'})
        for manager in (self.manager,other_manager):
            self.store.decide(manager,'quote-1',True)
            self.store.execute(manager,'quote-1')
            self.assertEqual(self.store.effect_count(manager),1)
        self.assertEqual(self.store.get_request(other_sales,'quote-1')['plan']['total'],'100.00')
        with self.assertRaises(PermissionError):
            self.store.execute(FixtureActor(1,1,1,'manager'),'quote-1')
        with self.assertRaises(PermissionError): self.store.effect_count(self.sales)


if __name__=='__main__': unittest.main()
