import copy
import json
import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from enterprise_ai.document_samples import generate_corpus
from enterprise_ai.document_pipeline import ingest
from enterprise_ai.rag_index import snapshot, build_index, load_index, assert_fresh, validate_index
from enterprise_ai.rag_models import FixtureEncoder, vectors


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.source=self.root/'source'; self.state=self.root/'state.sqlite'
        self.catalog=generate_corpus(self.source,formats='md')
        self.assertEqual(ingest(self.source/'catalog.json',self.source,self.state,self.root/'import.json')['status'],'ok')
        self.encoder=FixtureEncoder()

    def build(self):
        return build_index(self.state,1,1,self.encoder,self.root/'index.json')

    def test_complete_snapshot_roundtrip_and_identity(self):
        s=snapshot(self.state,1)
        self.assertEqual(len(s['heads']),15)
        self.assertEqual(len(s['chunks']),105)
        pack=self.build()
        self.assertEqual(pack,load_index(self.root/'index.json'))
        self.assertEqual(pack['model']['kind'],'synthetic-fixture')
        self.assertTrue(all(len(v)==1024 for v in pack['vectors']))
        assert_fresh(pack,self.state)
        with self.assertRaises(FileExistsError): self.build()

    def test_metadata_update_invalidates_old_pack(self):
        pack=self.build(); c=copy.deepcopy(self.catalog)
        c['documents'][0].update(version=2,roles=['employee'])
        (self.source/'catalog.json').write_text(json.dumps(c),encoding='utf-8')
        ingest(self.source/'catalog.json',self.source,self.state,self.root/'update.json')
        with self.assertRaisesRegex(ValueError,'stale'): assert_fresh(pack,self.state)
        new=build_index(self.state,1,2,self.encoder,self.root/'new.json')
        self.assertNotEqual(pack['source_digest'],new['source_digest'])

    def test_empty_tenant_and_tombstone_are_valid_snapshots(self):
        s=snapshot(self.state,99); self.assertEqual(s['chunks'],[])
        pack=build_index(self.state,99,1,self.encoder,self.root/'empty.json'); self.assertEqual(pack['vectors'],[])
        deletion=[{k:d[k] for k in ('tenant_id','document_id','version','op')} for d in self.catalog['documents'] if d['tenant_id']==1]
        for d in deletion: d.update(version=2,op='delete')
        (self.source/'catalog.json').write_text(json.dumps({'schema_version':'m08-catalog-v1','documents':deletion}))
        ingest(self.source/'catalog.json',self.source,self.state,self.root/'delete.json')
        s=snapshot(self.state,1); self.assertEqual(len(s['heads']),15); self.assertEqual(s['chunks'],[])

    def test_source_and_pack_tampering_rejected(self):
        pack=self.build()
        for change in ('text','vector','partial'):
            bad=copy.deepcopy(pack)
            if change=='text': bad['chunks'][0]['text']='forged'
            elif change=='vector': bad['vectors'][0][0]=float('nan')
            else: bad['chunks'].pop()
            with self.assertRaises(ValueError): validate_index(bad)
        with closing(sqlite3.connect(self.state)) as db,db:
            db.execute("UPDATE chunks SET payload=? WHERE tenant_id=1 AND document_id='crm-0001' AND ordinal=0",(json.dumps(pack['chunks'][1]),))
        with self.assertRaises(ValueError): snapshot(self.state,1)

    def test_changed_source_during_embedding_rejected_without_output(self):
        parent=self
        class Mutating(FixtureEncoder):
            def encode(self,texts):
                with closing(sqlite3.connect(parent.state)) as db,db:
                    db.execute("DELETE FROM heads WHERE tenant_id=1 AND document_id='crm-0001'")
                return super().encode(texts)
        with self.assertRaises(ValueError): build_index(self.state,1,1,Mutating(),self.root/'bad.json')
        self.assertFalse((self.root/'bad.json').exists())

    def test_vector_validation(self):
        good=[1.0]+[0.0]*1023
        self.assertEqual(vectors([good],1),[good])
        for bad in ([0.0]*1024,[1.0]*1024,[math.inf]+[0.0]*1023,[True]+[0.0]*1023,[1.0]):
            with self.assertRaises(ValueError): vectors([bad],1)
        with self.assertRaises(ValueError): vectors([],1)


if __name__=='__main__': unittest.main()
