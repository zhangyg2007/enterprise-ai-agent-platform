import copy
import hashlib
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from enterprise_ai.document_pipeline import chunk_segments, ingest, export_chunks
from enterprise_ai.document_samples import generate_corpus


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'sources'
        self.catalog = generate_corpus(self.source,formats='md')
        self.manifest = self.source/'catalog.json'
        self.state = self.root/'state.sqlite'
        self.counter = 0

    def ingest(self, catalog=None, **kwargs):
        self.counter += 1
        if catalog is not None:
            self.manifest.write_text(json.dumps(catalog,ensure_ascii=False),encoding='utf-8')
        return ingest(self.manifest,self.source,self.state,self.root/f'report-{self.counter}.json',**kwargs)

    def export(self, tenant=1, roles=('sales',), as_of='2026-10-21'):
        self.counter += 1
        out = self.root/f'chunks-{self.counter}.jsonl'
        report = export_chunks(self.state,out,tenant_id=tenant,roles=list(roles),as_of=as_of)
        raw = out.read_bytes()
        self.assertEqual(report['sha256'],hashlib.sha256(raw).hexdigest())
        return [json.loads(line) for line in raw.splitlines()]

    def test_chunks_cover_source_with_stable_bounded_coordinates(self):
        meta = self.catalog['documents'][0]
        text = ('一段业务文字。'*100)+'end'
        segments = [{'text':text,'page':2,'section':'规则','block':1}]
        chunks = chunk_segments(meta,segments,max_chars=80,overlap=10)
        self.assertEqual(chunks,chunk_segments(meta,segments,max_chars=80,overlap=10))
        covered = set()
        for c in chunks:
            self.assertLessEqual(len(c['text']),80)
            self.assertEqual(c['text'],text[c['start']:c['end']])
            covered.update(range(c['start'],c['end']))
            self.assertEqual(c['page'],2)
            self.assertIsNone(c['embedding_model_revision'])
        self.assertEqual(covered,set(range(len(text))))
        changed = chunk_segments(meta,segments,max_chars=90,overlap=10)
        self.assertNotEqual(chunks[0]['chunk_id'],changed[0]['chunk_id'])
        for args in ({'max_chars':79},{'overlap':200},{'max_chars':True},{'overlap':-1}):
            with self.assertRaises(ValueError):
                chunk_segments(meta,segments,**args)

    def test_idempotent_and_authorized_export(self):
        self.assertEqual(self.ingest()['created'],30)
        second = self.ingest()
        self.assertEqual(second['unchanged'],30)
        self.assertEqual(second['status'],'ok')
        chunks = self.export()
        self.assertEqual(len({c['document_id'] for c in chunks}),5)
        self.assertTrue(all(c['tenant_id']==1 and 'sales' in c['roles'] for c in chunks))
        self.assertEqual(self.export(roles=('employee',)),[])
        self.assertEqual(self.export(as_of='2025-12-31'),[])

    def test_version_acl_dates_and_delete(self):
        self.ingest()
        d = copy.deepcopy(self.catalog['documents'][0])
        d.update(version=2,roles=['employee'],effective_from='2026-11-01',effective_to='2026-12-01')
        self.assertEqual(self.ingest({'schema_version':'m08-catalog-v1','documents':[d]})['updated'],1)
        self.assertNotIn(d['document_id'],{c['document_id'] for c in self.export()})
        self.assertEqual(self.export(roles=('employee',)),[])
        self.assertTrue(self.export(roles=('employee',),as_of='2026-11-01'))
        self.assertEqual(self.export(roles=('employee',),as_of='2026-12-01'),[])
        deletion = {k:d[k] for k in ('tenant_id','document_id','version','op')}
        deletion.update(version=3,op='delete')
        cat = {'schema_version':'m08-catalog-v1','documents':[deletion]}
        self.assertEqual(self.ingest(cat)['deleted'],1)
        self.assertEqual(self.ingest(cat)['unchanged'],1)
        self.assertEqual(self.export(roles=('employee',),as_of='2026-11-01'),[])
        with closing(sqlite3.connect(self.state)) as db, db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM versions').fetchone()[0],32)
            self.assertGreater(db.execute('SELECT COUNT(*) FROM chunks WHERE version=1').fetchone()[0],0)

    def test_failed_batch_rolls_back_all_heads(self):
        self.ingest()
        cat = copy.deepcopy(self.catalog)
        cat['documents'][0]['version'] = 2
        cat['documents'][0]['roles'] = ['employee']
        cat['documents'][-1]['title'] += ' change without version'
        before = self.export()
        report = self.ingest(cat)
        self.assertEqual(report['status'],'failed')
        self.assertEqual(report['updated'],0)
        self.assertEqual(before,self.export())
        with closing(sqlite3.connect(self.state)) as db, db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM versions').fetchone()[0],30)

    def test_invalid_metadata_duplicates_and_unknown_delete(self):
        self.ingest()
        for field,value in [('tenant_id',True),('roles',[]),('roles',['sales','sales']),('version',0),
                            ('document_id','../bad'),('effective_to','2025-01-01'),('extra','x')]:
            cat = copy.deepcopy(self.catalog); cat['documents'][0][field] = value
            self.assertEqual(self.ingest(cat)['status'],'failed',field)
        cat = copy.deepcopy(self.catalog); cat['documents'].append(cat['documents'][0])
        self.assertEqual(self.ingest(cat)['status'],'failed')
        cat = {'schema_version':'m08-catalog-v1','documents':[{'tenant_id':1,'document_id':'missing','version':2,'op':'delete'}]}
        self.assertEqual(self.ingest(cat)['status'],'failed')

    def test_hash_paths_and_source_failure_do_not_publish(self):
        self.ingest()
        for value in ('../outside.md','/absolute.md','C:/outside.md','tenant-1\\crm-0001.md','tenant-1/./crm-0001.md'):
            cat = copy.deepcopy(self.catalog); cat['documents'][0]['source_path'] = value
            self.assertEqual(self.ingest(cat)['status'],'failed',value)
        cat = copy.deepcopy(self.catalog); cat['documents'][0]['sha256'] = '0'*64
        self.assertEqual(self.ingest(cat)['status'],'failed')
        cat = copy.deepcopy(self.catalog)
        (self.source/cat['documents'][-1]['source_path']).unlink()
        self.assertEqual(self.ingest(cat)['status'],'failed')
        self.assertTrue(self.export())

    def test_same_version_pipeline_change_and_rollback_rejected(self):
        self.ingest()
        self.assertEqual(self.ingest(max_chars=200)['status'],'failed')
        d = copy.deepcopy(self.catalog['documents'][0]); d['version']=2
        self.ingest({'schema_version':'m08-catalog-v1','documents':[d]})
        self.assertEqual(self.ingest(self.catalog)['status'],'failed')

    def test_output_exclusive_and_foreign_database(self):
        report = self.root/'existing.json'; report.write_text('keep')
        with self.assertRaises(FileExistsError):
            ingest(self.manifest,self.source,self.state,report)
        self.assertFalse(self.state.exists())
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('CREATE TABLE unrelated (x TEXT)')
        self.assertEqual(self.ingest()['status'],'failed')
        with closing(sqlite3.connect(self.state)) as db, db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[('unrelated',)])

    def test_bytes_snapshot_is_parsed_after_verification(self):
        from enterprise_ai.document_parse import parse_document
        cat = {'schema_version':'m08-catalog-v1','documents':[self.catalog['documents'][0]]}
        path = self.source/cat['documents'][0]['source_path']
        original = path.read_bytes()
        def mutate_then_parse(raw,suffix):
            path.write_bytes(b'changed later')
            self.assertEqual(raw,original)
            return parse_document(raw,suffix)
        with patch('enterprise_ai.document_pipeline.parse_document',side_effect=mutate_then_parse):
            self.assertEqual(self.ingest(cat)['status'],'ok')
        self.assertNotIn('changed later',''.join(c['text'] for c in self.export()))

    def test_export_rejects_bad_identity_and_existing_output(self):
        self.ingest()
        for kwargs in ({'tenant_id':True,'roles':['sales'],'as_of':'2026-01-01'},
                       {'tenant_id':1,'roles':[],'as_of':'2026-01-01'},
                       {'tenant_id':1,'roles':['admin'],'as_of':'2026-01-01'}):
            with self.assertRaises(ValueError):
                export_chunks(self.state,self.root/'invalid.jsonl',**kwargs)
        out = self.root/'existing.jsonl'; out.write_text('keep')
        with self.assertRaises(FileExistsError):
            export_chunks(self.state,out,tenant_id=1,roles=['sales'],as_of='2026-01-01')
        self.assertEqual(out.read_text(),'keep')

    @unittest.skipUnless(importlib.util.find_spec('docx'), 'python-docx optional')
    def test_formula_rejection_preserves_existing_heads(self):
        from docx import Document
        from docx.oxml import OxmlElement
        self.ingest()
        before = self.export()
        doc = Document(); paragraph = doc.add_paragraph('Charge calculation: ')
        math = OxmlElement('m:oMath'); run = OxmlElement('m:r'); text = OxmlElement('m:t')
        text.text = 'fee = principal * 0.15'; run.append(text); math.append(run); paragraph._p.append(math)
        raw = io.BytesIO(); doc.save(raw)
        path = self.source/'formula.docx'; path.write_bytes(raw.getvalue())
        cat = copy.deepcopy(self.catalog)
        cat['documents'][0].update(version=2,roles=['employee'])
        cat['documents'][1].update(version=2,source_path='formula.docx',sha256=hashlib.sha256(raw.getvalue()).hexdigest())
        self.assertEqual(self.ingest(cat)['status'],'failed')
        self.assertEqual(before,self.export())
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM versions').fetchone()[0],30)

    def test_report_write_failure_recovers_with_idempotent_new_report(self):
        with patch('enterprise_ai.document_pipeline._write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.ingest()
        # The commit succeeded; no misleading failed/no-changes report is returned.
        self.assertTrue(self.export())
        result = self.ingest()
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['unchanged'],30)


if __name__ == '__main__':
    unittest.main()
