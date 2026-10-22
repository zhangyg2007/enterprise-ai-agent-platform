import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enterprise_ai.document_samples import generate_corpus
from enterprise_ai.document_pipeline import ingest
from enterprise_ai.rag_index import build_index
from enterprise_ai.rag_models import FixtureEncoder
from enterprise_ai.rag_eval import evaluate, score_rows, load_cases, run_demo


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'source'
        generate_corpus(source, formats='md')
        self.state = self.root / 'state.sqlite'
        ingest(source/'catalog.json', source, self.state, self.root/'ingest.json')
        self.encoder = FixtureEncoder()
        self.index = build_index(self.state, 1, 1, self.encoder, self.root/'index.json')

    def test_document_rank_deduplicates_chunks_and_keeps_errors_in_denominator(self):
        target = {'tenant_id': 1, 'document_id': 'crm-0001', 'version': 1}
        other = dict(target, document_id='crm-0002')
        rows = [
            {'expected_documents': [target], 'documents': [other, other, target], 'status': 'ok'},
            {'expected_documents': [target], 'documents': [], 'status': 'error'},
            {'expected_documents': [], 'documents': [other], 'status': 'ok'},
            {'expected_documents': [], 'documents': [], 'status': 'error'},
        ]
        metrics = score_rows(rows)
        self.assertEqual(metrics['answerable_questions'], 2)
        self.assertEqual(metrics['recall_at_k'], .5)
        self.assertEqual(metrics['mrr_at_k'], .25)
        self.assertEqual(metrics['errors'], 2)
        self.assertEqual(metrics['rejection_questions'], 2)
        self.assertEqual(metrics['rejected_candidate_rate'], .5)
        self.assertEqual(metrics['clean_rejection_rate'], 0)

    def test_evaluation_retains_citations_and_runtime_failures(self):
        cases = load_cases(Path('data/samples/m09-retrieval.json'), tenant=1)
        report = evaluate(self.index, self.state, cases, self.encoder, self.root/'report.json', mode='keyword')
        self.assertGreaterEqual(len(report['rows']), 12)
        self.assertEqual(report['kind'], 'fixture-pipeline-check-not-semantic-quality')
        self.assertEqual(report['metrics']['errors'], 0)
        self.assertTrue(any(row['citations'] for row in report['rows']))
        row = next(row for row in report['rows'] if row['citations'])
        self.assertTrue(all(c['chunk_id'] in row['chunk_ids'] for c in row['citations']))
        with patch('enterprise_ai.rag_eval.search', side_effect=TimeoutError('not for public report')):
            failed = evaluate(self.index, self.state, cases, self.encoder, self.root/'failed.json')
        self.assertEqual(failed['metrics']['errors'], len(cases))
        self.assertEqual(failed['metrics']['recall_at_k'], 0)
        self.assertNotIn('not for public report', json.dumps(failed))

    def test_bad_labels_duplicates_and_wrong_tenant_fail_before_output(self):
        cases = load_cases(Path('data/samples/m09-retrieval.json'), tenant=1)
        for invalid in [cases + [cases[0]], [], [dict(cases[0], tenant_id=2)],
                        [dict(cases[0], expected_documents=[{'tenant_id': 2, 'document_id': 'crm-0016', 'version': 1}])]]:
            with self.assertRaises(ValueError):
                evaluate(self.index, self.state, invalid, self.encoder, self.root/'bad.json')
            self.assertFalse((self.root/'bad.json').exists())
        evaluate(self.index, self.state, cases, self.encoder, self.root/'once.json')
        with self.assertRaises(FileExistsError):
            evaluate(self.index, self.state, cases, self.encoder, self.root/'once.json')

    def test_changed_source_during_evaluation_cannot_publish_valid_report(self):
        cases = load_cases(Path('data/samples/m09-retrieval.json'), tenant=1)
        def change(*args, **kwargs):
            delete = self.root/'delete.json'
            delete.write_text(json.dumps({'schema_version': 'm08-catalog-v1', 'documents': [
                {'tenant_id': 1, 'document_id': 'crm-0001', 'version': 2, 'op': 'delete'}]}))
            ingest(delete, self.root/'source', self.state, self.root/'deleted.json')
            raise ValueError('changed')
        with patch('enterprise_ai.rag_eval.search', side_effect=change):
            with self.assertRaises(ValueError):
                evaluate(self.index, self.state, cases[:1], self.encoder, self.root/'stale.json')
        self.assertFalse((self.root/'stale.json').exists())

    def test_demo_proves_revocation_deletion_and_rebuild(self):
        result = run_demo(self.root/'demo')
        self.assertEqual(result['checks_passed'], 6)
        self.assertTrue(result['stale_index_rejected'])
        self.assertTrue(result['new_role_version2_visible'])
        self.assertTrue(result['deleted_document_absent'])
        self.assertTrue((self.root/'demo'/'summary.json').exists())
        with self.assertRaises(FileExistsError): run_demo(self.root/'demo')


if __name__ == '__main__': unittest.main()
