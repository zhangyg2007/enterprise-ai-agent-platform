import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from enterprise_ai.document_samples import generate_corpus
from enterprise_ai.document_pipeline import ingest
from enterprise_ai.rag_index import build_index
from enterprise_ai.rag_models import FixtureEncoder
from enterprise_ai.four_arm_eval import freeze_from_m09
from enterprise_ai import four_arm_freeze
from enterprise_ai import four_arm_eval


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root/'sources'
        generate_corpus(source, formats='md')
        self.state = self.root/'state.sqlite'
        ingest(source/'catalog.json', source, self.state, self.root/'ingest.json')
        self.encoder = FixtureEncoder()
        self.index = build_index(self.state, 1, 1, self.encoder, self.root/'index.json')
        self.seed = {'cases': [{'id': 'quote', 'question': '报价制度是什么？',
                               'policy_query': '报价审批', 'role': 'sales', 'as_of': '2026-10-22',
                               'tool_snapshot': {'amount': '190.00'}}]}

    def test_snapshot_contains_actual_m09_citations_and_provenance(self):
        result = freeze_from_m09(self.seed, self.index, self.state, self.encoder)
        self.assertEqual(result['kind'], 'synthetic-fixture')
        self.assertEqual(result['provenance']['index_digest'], self.index['digest'])
        self.assertTrue(result['cases'][0]['citations'])
        citation = result['cases'][0]['citations'][0]
        self.assertEqual(citation['tenant_id'], 1)
        self.assertTrue(citation['chunk_id'])
        self.assertEqual(result['cases'][0]['tool_snapshot'], {'amount': '190.00'})

    def test_seed_cannot_inject_identity_or_arbitrary_fields(self):
        seed = json.loads(json.dumps(self.seed))
        seed['cases'][0]['tenant_id'] = 2
        with self.assertRaises(ValueError):
            freeze_from_m09(seed, self.index, self.state, self.encoder)
        seed = json.loads(json.dumps(self.seed))
        seed['cases'][0]['role'] = 'manager'
        with self.assertRaises(ValueError):
            freeze_from_m09(seed, self.index, self.state, self.encoder)

    def test_cli_passes_requested_cuda_device_to_local_encoder(self):
        seed = self.root/'seed.json'
        seed.write_text(json.dumps(self.seed, ensure_ascii=False), encoding='utf-8')
        output = self.root/'frozen.json'
        with patch.object(four_arm_freeze, 'LocalEncoder', return_value=self.encoder) as encoder, \
                patch.object(four_arm_freeze, 'load_index', return_value=self.index):
            status = four_arm_freeze.main(['--seed',str(seed),'--index','ignored', '--state',str(self.state),
                                          '--output',str(output), '--embedding','/model', '--device','cuda'])
        self.assertEqual(status, 0)
        encoder.assert_called_once_with(Path('/model'), device='cuda')

    def test_oversized_frozen_snapshot_fails_before_output_creation(self):
        seed = self.root/'size-seed.json'
        seed.write_text(json.dumps(self.seed, ensure_ascii=False), encoding='utf-8')
        output = self.root/'too-large.json'
        with patch.object(four_arm_eval, 'MAX_SNAPSHOT_BYTES', 300, create=True), \
                patch.object(four_arm_freeze, 'load_index', return_value=self.index):
            status = four_arm_freeze.main(['--seed',str(seed),'--index','ignored','--state',str(self.state),
                                           '--output',str(output),'--fixture-encoder'])
        self.assertEqual(status, 1)
        self.assertFalse(output.exists())
