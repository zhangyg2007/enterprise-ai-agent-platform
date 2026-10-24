"""Offline SQL/driver contracts, never a claim of PostgreSQL integration."""
from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import patch

from enterprise_ai.workflow_store import FixtureActor
from enterprise_ai.workflow_pg import PgStore, FixedConnection, identity, service_name


class Cursor:
    def __init__(self, rows): self.rows = rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


class Connection:
    def __init__(self, **changes):
        self.calls = []; self.committed = False; self.rolled_back = False
        self.context = dict(login='employee', effective='employee', tenant_id=1,
                            user_id=2, employee_id=1, role='employee', unsafe=False)
        self.context.update(changes)
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if 'workflow_auth.context()' in sql: return Cursor([self.context])
        if 'lab_id' in sql: return Cursor([{'lab_id':'a'*32, 'format_version':1}])
        if 'count(*)' in sql: return Cursor([{'count':0}])
        if 'FROM workflow_lab.requests' in sql: return Cursor([])
        return Cursor([])
    @contextmanager
    def transaction(self):
        try: yield self
        except BaseException: self.rolled_back = True; raise
        else: self.committed = True
    def __enter__(self): return self
    def __exit__(self, *args): pass


class PgContractTests(unittest.TestCase):
    def test_identity_is_database_login_mapping_not_client_setting(self):
        self.assertEqual(identity(Connection()), FixtureActor(1,2,1,'employee'))
        for changes in ({'effective':'manager'}, {'unsafe':True}, {'tenant_id':None}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, PermissionError)):
                identity(Connection(**changes))

    def test_service_names_reject_conninfo_or_injection(self):
        self.assertEqual(service_name('m11-employee'), 'm11-employee')
        for bad in ('', 'host=evil', 'a b', 'a\npassword=x', '../secret'):
            with self.assertRaises(ValueError): service_name(bad)

    def test_fixed_adapter_preserves_bind_parameters_and_both_row_accesses(self):
        db=Connection()
        result=FixedConnection(db).execute('SELECT count(*) FROM effects WHERE tenant=?', (1,)).fetchone()
        self.assertEqual(result[0],0); self.assertEqual(result['count'],0)
        self.assertEqual(db.calls[-1],('SELECT count(*) FROM workflow_lab.effects WHERE tenant=%s',(1,)))
        with self.assertRaises(ValueError): FixedConnection(db).execute('DROP TABLE effects')

    def test_bound_actor_rejected_without_opening_another_connection(self):
        with patch('enterprise_ai.workflow_pg.connection',return_value=Connection()) as connect:
            store=PgStore('employee')
            count=connect.call_count
            with self.assertRaises(PermissionError):
                store.get_request(FixtureActor(1,4,4,'manager'),'x')
            self.assertEqual(connect.call_count,count)

    def test_transaction_checks_identity_then_serializes_tenant_and_rolls_back(self):
        db=Connection()
        with patch('enterprise_ai.workflow_pg.connection',return_value=db):
            store=PgStore('employee')
            with self.assertRaisesRegex(RuntimeError,'probe'):
                with store._transaction() as tx:
                    tx.execute('SELECT count(*) FROM effects WHERE tenant=?',(1,))
                    raise RuntimeError('probe')
        self.assertTrue(db.rolled_back)
        locks=[p for sql,p in db.calls if 'pg_advisory_xact_lock' in sql]
        self.assertEqual(locks,[(7711,1)])

    def test_reconnected_identity_change_is_denied(self):
        with patch('enterprise_ai.workflow_pg.connection',side_effect=[Connection(),Connection(user_id=9)]):
            store=PgStore('employee')
            with self.assertRaises(PermissionError): store.get_request(store.actor,'x')

    def test_migration_has_session_user_rls_and_no_source_mutation(self):
        text=Path('infra/postgres/migrations/030-m11-workflows.sql').read_text()
        for token in ('session_user','SECURITY DEFINER','ENABLE ROW LEVEL SECURITY',
                      'REVOKE ALL','CREATE SCHEMA workflow_cp','UNIQUE(tenant,idem_key)'):
            self.assertIn(token,text)
        for table in ('crm.customers','hcm.leave_balances','scm.inventory'):
            self.assertNotIn('UPDATE '+table,text)
        self.assertNotIn('current_setting(',text)

    def test_postgres_graph_uses_real_saver_after_authorization_and_same_lab_check(self):
        from enterprise_ai.workflow_pg import PgWorkflowLab
        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        self.assertTrue(issubclass(PgWorkflowLab, __import__('enterprise_ai.workflow_graph',fromlist=['WorkflowLab']).WorkflowLab))
        db=Connection()
        # Actual framework class, with a mocked transport only; no PG claim.
        with patch('enterprise_ai.workflow_pg.connection',return_value=db), \
             patch('enterprise_ai.workflow_pg.check_checkpoint_identity'), \
             patch('enterprise_ai.workflow_pg.lab_identity',return_value='a'*32):
            lab=PgWorkflowLab('employee','checkpoint')
            row={'tenant':1,'thread_id':'leave-1','request_id':'leave-1'}
            with lab._graph(lab.store.actor,row) as graph:
                self.assertIsInstance(graph.checkpointer,PostgresSaver)
                self.assertIsInstance(graph.checkpointer.serde,JsonPlusSerializer)
            with patch('enterprise_ai.workflow_pg.lab_identity',return_value='b'*32), self.assertRaises(ValueError):
                with lab._graph(lab.store.actor,row): pass

    def test_checkpoint_setup_is_explicit_and_not_runtime_ddl(self):
        from enterprise_ai.workflow_pg import setup_checkpoints
        db=Connection()
        with patch('enterprise_ai.workflow_pg.connection',return_value=db), \
             patch('enterprise_ai.workflow_pg.require_migrator'), \
             patch('langgraph.checkpoint.postgres.PostgresSaver.setup') as setup:
            setup_checkpoints('migration')
        setup.assert_called_once()

    def test_postgres_graph_denies_unauthorized_before_checkpoint_connection(self):
        from enterprise_ai.workflow_pg import PgWorkflowLab
        with patch('enterprise_ai.workflow_pg.connection',return_value=Connection()):
            lab=PgWorkflowLab('employee','checkpoint')
        with patch.object(lab,'_locked',return_value=__import__('contextlib').nullcontext()), \
             patch.object(lab,'_authorize',side_effect=PermissionError('denied')), \
             patch('enterprise_ai.workflow_pg.connection') as cp:
            with self.assertRaises(PermissionError): lab.inspect(lab.store.actor,'x')
            cp.assert_not_called()


if __name__ == '__main__': unittest.main()
