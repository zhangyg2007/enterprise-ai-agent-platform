import copy
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from enterprise_ai.document_samples import generate_corpus
from enterprise_ai.document_pipeline import ingest, _digest
from enterprise_ai.rag_index import build_index
from enterprise_ai.rag_models import FixtureEncoder, ENCODER_REVISION
from enterprise_ai.rag_pg import load, retrieve, dense_sql, check_reader


class Cursor:
    def __init__(self,rows=()): self.rows=rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)


class FakeConnection:
    def __init__(self,index,existing=None): self.index=index; self.existing=existing; self.calls=[]
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def transaction(self): return nullcontext()
    def execute(self,sql,params=None):
        self.calls.append((sql,params))
        if 'rolsuper' in sql: return Cursor([(False,False)])
        if 'auth.current_tenant()' in sql and sql.startswith('SELECT'): return Cursor([(1,['sales'])])
        if 'FROM rag_lab.collections' in sql: return Cursor([self.existing] if self.existing else [])
        if sql.startswith('EXPLAIN'): return Cursor([([{'Plan':{'Node Type':'Seq Scan'}}],)])
        if 'SELECT payload FROM rag_lab.chunks' in sql:
            return Cursor([(c,) for c in self.index['chunks'] if c['domain']=='crm'])
        if 'embedding <=>' in sql:
            return Cursor([(c['chunk_id'],0.1) for c in self.index['chunks'] if c['domain']=='crm'][:20])
        return Cursor()


class PgTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
        src=self.root/'source'; self.state=self.root/'state.sqlite'; generate_corpus(src,formats='md')
        ingest(src/'catalog.json',src,self.state,self.root/'report.json')
        self.encoder=FixtureEncoder(); self.pack=build_index(self.state,1,1,self.encoder,self.root/'index.json')

    def real_pack(self):
        pack=copy.deepcopy(self.pack)
        pack['model'].update(kind='local-transformers',model='BAAI/bge-m3',revision=ENCODER_REVISION,pooling='cls',assets={'weight':{'sha256':'a'*64}},max_tokens=1024)
        pack['digest']=_digest({k:v for k,v in pack.items() if k!='digest'})
        return pack

    def test_fixture_rejected_before_connect(self):
        with patch('enterprise_ai.rag_pg.connect') as connector:
            with self.assertRaises(ValueError): load({},'demo',self.pack,self.state)
            connector.assert_not_called()

    def test_load_uses_bound_values_and_generation_guard(self):
        pack=self.real_pack(); conn=FakeConnection(pack)
        with patch('enterprise_ai.rag_pg.connect',return_value=conn):
            report=load({},'demo',pack,self.state)
        self.assertEqual(report['status'],'loaded')
        inserts=[c for c in conn.calls if c[0].startswith('INSERT INTO rag_lab.chunks')]
        self.assertEqual(len(inserts),len(pack['chunks']))
        self.assertTrue(all('%s' in sql and len(params)>5 for sql,params in inserts))
        conn=FakeConnection(pack,(1,2,'different',pack['model']))
        with patch('enterprise_ai.rag_pg.connect',return_value=conn):
            with self.assertRaises(ValueError): load({},'demo',pack,self.state)
        self.assertFalse(any(sql.startswith('DELETE') for sql,_ in conn.calls))

    def test_dense_sql_filter_and_query_parameterization(self):
        query="报价'; DROP TABLE x; --"
        sql=dense_sql()
        self.assertNotIn(query,sql)
        for key in ('tenant_id','roles &&','effective_from','effective_to','ORDER BY embedding <=>'):
            self.assertIn(key,sql)

    def test_reader_must_match_tenant_and_role(self):
        conn=FakeConnection(self.pack)
        check_reader(conn,1,['sales'])
        for tenant,roles in ((2,['sales']),(1,['hr'])):
            with self.assertRaises(ValueError): check_reader(conn,tenant,roles)

    def test_requested_hnsw_retains_actual_plan_and_authorized_results(self):
        pack=self.real_pack(); encoder=FixtureEncoder(); encoder.identity=pack['model']
        conn=FakeConnection(pack,(1,1,pack['digest'],pack['model']))
        with patch('enterprise_ai.rag_pg.connect',return_value=conn):
            result=retrieve({},'a','demo',pack,self.state,'报价',roles=['sales'],as_of='2026-10-21',method='hnsw',encoder=encoder)
        self.assertEqual(result['diagnostics']['requested_method'],'hnsw')
        self.assertEqual(result['diagnostics']['plan'][0]['Plan']['Node Type'],'Seq Scan')
        self.assertTrue(all(h['chunk']['domain']=='crm' for h in result['hits']))
        self.assertTrue(any('strict_order' in sql for sql,_ in conn.calls))

    def test_schema_is_additive_and_role_mapping_is_not_session_variable(self):
        sql=Path('infra/postgres/migrations/020-m09-rag.sql').read_text()
        self.assertIn('ENABLE ROW LEVEL SECURITY',sql)
        self.assertIn('session_user',sql)
        self.assertIn('REVOKE ALL',sql)
        self.assertNotIn('current_setting(',sql)
        self.assertNotIn('DROP ',sql)


if __name__=='__main__': unittest.main()
