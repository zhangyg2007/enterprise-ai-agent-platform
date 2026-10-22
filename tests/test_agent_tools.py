import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from enterprise_ai.agent import Principal
from enterprise_ai.agent_tools import LiveTools, FixtureTools
from enterprise_ai.document_samples import generate_corpus
from enterprise_ai.document_pipeline import ingest
from enterprise_ai.rag_index import build_index
from enterprise_ai.rag_models import FixtureEncoder


class Conn:
    def __init__(self, identity=(1,1)): self.identity=identity; self.calls=[]
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def transaction(self): return nullcontext()
    def execute(self,sql,params=None):
        self.calls.append((sql,params))
        if 'rolsuper' in sql: return SimpleNamespace(fetchone=lambda:(False,False))
        if 'auth.current_tenant(),' in sql: return SimpleNamespace(fetchone=lambda:self.identity)
        return SimpleNamespace(description=[SimpleNamespace(name='remaining_days')],fetchmany=lambda n:[(15,)])


class ToolTests(unittest.TestCase):
    def test_leave_uses_bound_year_and_database_identity_not_model_employee(self):
        principal=Principal(1,'a',('employee',),as_of='2026-10-22')
        conn=Conn()
        with patch('enterprise_ai.agent_tools.connect',return_value=conn):
            r=LiveTools({}).call(principal,{'tool':'leave','arguments':{}})
        self.assertEqual(r['rows'],[{'remaining_days':15}])
        query=next((s,p) for s,p in conn.calls if 'leave_balances' in s)
        self.assertEqual(query[1],(2026,)); self.assertIn('auth.current_employee()',query[0])
        self.assertTrue(any('READ ONLY' in s for s,p in conn.calls))
        self.assertTrue(any('statement_timeout' in s for s,p in conn.calls))
        with patch('enterprise_ai.agent_tools.connect',return_value=Conn((2,1))):
            with self.assertRaises(PermissionError): LiveTools({}).call(principal,{'tool':'leave','arguments':{}})
        with self.assertRaises(ValueError): LiveTools({}).call(principal,{'tool':'leave','arguments':{'employee_id':2}})

    def test_fixture_uses_actual_rag_and_rejects_stale_source(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); src=root/'sources'; generate_corpus(src,formats='md')
            state=root/'state.sqlite'; ingest(src/'catalog.json',src,state,root/'load.json')
            encoder=FixtureEncoder(); index=build_index(state,1,1,encoder,root/'index.json')
            backend=FixtureTools(index,state,encoder); p=Principal(1,'a',('sales',))
            r=backend.call(p,{'tool':'knowledge','arguments':{'query':'报价审批'}})
            self.assertTrue(r['answer']['citations']); self.assertEqual(r['kind'],'synthetic-fixture')
            with self.assertRaises(PermissionError): backend.call(Principal(2,'b',('sales',)),{'tool':'knowledge','arguments':{'query':'报价审批'}})
            import json
            delete=root/'delete.json'; delete.write_text(json.dumps({'schema_version':'m08-catalog-v1','documents':[{'tenant_id':1,'document_id':'crm-0001','version':2,'op':'delete'}]}))
            ingest(delete,src,state,root/'delete-report.json')
            with self.assertRaisesRegex(ValueError,'stale'): backend.call(p,{'tool':'knowledge','arguments':{'query':'报价审批'}})


if __name__=='__main__': unittest.main()
