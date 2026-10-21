import tempfile
import unittest
from pathlib import Path

from enterprise_ai.document_samples import generate_corpus
from enterprise_ai.document_pipeline import ingest
from enterprise_ai.rag_index import build_index
from enterprise_ai.rag_models import FixtureEncoder, tokens
from enterprise_ai.rag_search import search, evidence_answer, fuse


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); source=self.root/'source'; self.state=self.root/'state.sqlite'
        generate_corpus(source,formats='md'); ingest(source/'catalog.json',source,self.state,self.root/'report.json')
        self.encoder=FixtureEncoder(); self.index=build_index(self.state,1,1,self.encoder,self.root/'index.json')

    def query(self,**kwargs):
        return search(self.index,self.state,'报价折扣审批',roles=['sales'],as_of='2026-10-21',encoder=self.encoder,**kwargs)

    def test_modes_return_authorized_citations(self):
        for mode in ('dense','keyword','hybrid'):
            result=self.query(mode=mode)
            self.assertTrue(result['hits'])
            self.assertTrue(all(c['chunk']['tenant_id']==1 and c['chunk']['domain']=='crm' for c in result['hits']))
            answer=evidence_answer(result)
            self.assertEqual(answer['status'],'evidence')
            self.assertTrue(all(c['document_id'].startswith('crm') for c in answer['citations']))
            for c in answer['citations']: self.assertIn(c['quote'],answer['answer'])

    def test_denied_date_and_no_evidence(self):
        for roles,date,q in ((['employee'],'2026-10-21','报价'),(['sales'],'2025-01-01','报价'),(['sales'],'2026-10-21','火星轨道周期')):
            result=search(self.index,self.state,q,roles=roles,as_of=date,encoder=self.encoder,mode='keyword')
            self.assertEqual(result['hits'],[])
            self.assertEqual(evidence_answer(result)['status'],'insufficient')

    def test_model_mismatch_bad_arguments_and_reranker(self):
        fake=FixtureEncoder(); fake.identity={**fake.identity,'revision':'other'}
        with self.assertRaises(ValueError): search(self.index,self.state,'报价',roles=['sales'],as_of='2026-10-21',encoder=fake)
        for args in ({'k':0},{'candidates':1,'k':5},{'mode':'sql'},{'k':True}):
            with self.assertRaises(ValueError): self.query(**args)
        class Bad:
            identity={'kind':'test'}
            def score(self,q,texts): return [float('nan')]*len(texts)
        with self.assertRaises(ValueError): self.query(reranker=Bad())

    def test_reranker_only_receives_authorized_candidates(self):
        parent=self
        class Spy:
            identity={'kind':'test'}
            def score(self,q,texts):
                parent.assertTrue(all('合成租户2' not in t and '休假' not in t for t in texts))
                return list(range(len(texts)))
        result=self.query(reranker=Spy())
        self.assertEqual(result['reranker'],{'kind':'test'})

    def test_tokens_and_rrf_are_deterministic(self):
        self.assertIn('报价',tokens('报价 ABC-12'))
        self.assertIn('abc',tokens('报价 ABC-12'))
        self.assertEqual(fuse(['a','b'],['b','c'])[0][0],'b')


if __name__=='__main__': unittest.main()
