import json
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_model_delivery as fixtures
from enterprise_ai import model_eval
from enterprise_ai.finetune_data import sha256, json_bytes


class EvaluationTests(unittest.TestCase):
    put = fixtures.DeliveryTests.put

    def setUp(self):
        fixtures.DeliveryTests.setUp(self)
        fixtures.DeliveryTests.prepare(self)
        self.cases = [json.loads(line) for line in (self.release / 'cases-test.jsonl').read_text(encoding='utf-8').splitlines()]
        self.answers = {c['input']: c['expected'] for c in self.cases}
        self.calls = []
        owner = self

        class Backend:
            runtime = {'backend': 'fixture', 'python': 'fixture', 'packages': {}}

            def chat(self, text, system):
                owner.calls.append(text)
                return {'text': json.dumps(owner.answers[text]), 'finish_reason': 'stop',
                        'prompt_tokens': 100, 'output_tokens': 30}
        self.backend = Backend

    def evaluate(self, name='before', variant='base', **kwargs):
        return model_eval.evaluate_release(self.release, variant, 'test', self.root / name,
                                           backend_factory=lambda config: self.backend(), **kwargs)

    def compare(self):
        return model_eval.compare_runs(self.root / 'before', self.root / 'after', self.root / 'comparison.json')

    def tamper(self, directory, name, value, rehash=True):
        path = self.root / directory
        raw = value if isinstance(value, bytes) else json_bytes(value)
        (path / name).write_bytes(raw)
        if rehash:
            manifest = json.loads((path / 'manifest.json').read_bytes())
            manifest['artifacts'][name] = sha256(raw)
            self.put(path / 'manifest.json', manifest)

    def test_complete_pair_has_all_records_and_recomputed_metrics(self):
        before = self.evaluate()
        after = self.evaluate('after', 'adapter')
        self.assertEqual(before['total'], 48)
        self.assertEqual(after['exact_match_rate'], 1)
        result = self.compare()
        self.assertEqual(result['exact_match_delta_pp'], 0)
        self.assertEqual(len(result['unchanged']), 48)
        self.assertEqual(result['text_equal_rate'], 1)
        with self.assertRaises(FileExistsError):
            self.compare()

    def test_first_generation_error_preserves_full_denominator(self):
        with patch.object(self.backend, 'chat', side_effect=RuntimeError('fixture failure')):
            summary = self.evaluate()
        self.assertEqual(summary['total'], 48)
        self.assertEqual(summary['request_errors'], 48)
        self.assertFalse(summary['comparison_eligible'])
        records = (self.root / 'before/records.jsonl').read_text().splitlines()
        self.assertEqual(len(records), 48)
        self.assertEqual(json.loads(records[1])['error'], 'not_executed')
        self.assertEqual(json.loads((self.root / 'before/manifest.json').read_bytes())['status'], 'failed')

    def test_initialization_error_records_all_cases(self):
        with patch.object(self.backend, '__init__', side_effect=ImportError('missing package')):
            summary = self.evaluate()
        self.assertEqual(summary['request_errors'], 48)
        self.assertEqual(summary['exact_match_rate'], 0)

    def test_subset_cannot_be_compared_and_existing_output_is_preserved(self):
        self.evaluate(limit=2)
        self.evaluate('after', 'adapter', limit=2)
        with self.assertRaises(ValueError):
            self.compare()
        with self.assertRaises(FileExistsError):
            self.evaluate()

    def test_precision_pair_and_runtime_mismatch_rejected(self):
        self.evaluate()
        self.evaluate('after', 'adapter-bf16')
        with self.assertRaises(ValueError):
            self.compare()
        self.evaluate('right', 'adapter')
        manifest = json.loads((self.root / 'right/manifest.json').read_bytes())
        manifest['runtime']['python'] = 'changed'
        self.put(self.root / 'right/manifest.json', manifest)
        with self.assertRaises(ValueError):
            model_eval.compare_runs(self.root / 'before', self.root / 'right', self.root / 'comparison.json')

    def test_summary_and_record_tampering_rejected_even_when_rehashed(self):
        self.evaluate()
        self.evaluate('after', 'adapter')
        summary = json.loads((self.root / 'after/summary.json').read_bytes())
        summary['exact_match_rate'] = 0
        self.tamper('after', 'summary.json', summary)
        with self.assertRaises(ValueError):
            self.compare()
        self.tamper('after', 'summary.json', json.loads((self.root / 'before/summary.json').read_bytes()), False)
        # A dropped row cannot be hidden by recomputing its checksum.
        lines = (self.root / 'before/records.jsonl').read_bytes().splitlines(keepends=True)
        self.tamper('before', 'records.jsonl', b''.join(lines[1:]))
        with self.assertRaises(ValueError):
            self.compare()

    def test_model_change_during_generation_invalidates_run(self):
        original = self.backend.chat
        def changed(instance, text, system):
            (self.adapter / 'adapter_model.safetensors').write_bytes(b'changed')
            return original(instance, text, system)
        with patch.object(self.backend, 'chat', changed):
            summary = self.evaluate()
        self.assertFalse(summary['identity_stable'])
        self.assertFalse(summary['comparison_eligible'])

    def test_truncation_gets_no_business_credit_and_refusal_leaks_counted(self):
        original = self.backend.chat
        def truncated(instance, text, system):
            value = original(instance, text, system)
            value['finish_reason'] = 'length'
            return value
        with patch.object(self.backend, 'chat', truncated):
            summary = self.evaluate()
        self.assertEqual(summary['exact_match_rate'], 0)
        self.assertEqual(summary['failures']['truncated'], 48)
        for case in self.cases:
            self.answers[case['input']] = dict(case['expected'], action='query')
        summary = self.evaluate('leaks', 'adapter')
        self.assertGreater(summary['refusal_false_accepts'], 0)

    def test_bf16_merge_comparison_records_exported_identity(self):
        import shutil
        shutil.copytree(self.model, self.release / 'merged-model')
        self.evaluate(variant='adapter-bf16')
        self.evaluate('after', 'merged')
        self.assertEqual(self.compare()['comparison'], 'export_regression')

    def test_invalid_backend_reply_is_error_not_success(self):
        with patch.object(self.backend, 'chat', return_value={'text': '{}', 'finish_reason': 'stop',
                                                            'prompt_tokens': True, 'output_tokens': 1}):
            self.assertEqual(self.evaluate()['request_errors'], 48)

    def test_cases_must_match_original_source_even_if_both_reports_are_edited(self):
        self.evaluate()
        self.evaluate('after', 'adapter')
        changed = [dict(c, input='changed ' + c['input']) for c in self.cases]
        for name in ('before', 'after'):
            self.tamper(name, 'cases.json', changed)
        with self.assertRaises(ValueError):
            self.compare()


class FactoryContractTests(unittest.TestCase):
    def test_context_checked_before_generation_using_actual_template(self):
        backend = model_eval.FactoryBackend.__new__(model_eval.FactoryBackend)
        template = SimpleNamespace(encode_oneturn=lambda *args: (list(range(1800)), []))
        engine = SimpleNamespace(template=template, tokenizer=object())
        backend.model = SimpleNamespace(engine=engine, chat=lambda *args, **kwargs: self.fail('must not generate'))
        with self.assertRaisesRegex(ValueError, 'context'):
            backend.chat('user', 'system')

    def test_response_mapping_and_generation_contract(self):
        from unittest.mock import Mock
        response = SimpleNamespace(response_text='{}', finish_reason='stop', prompt_length=3, response_length=2)
        engine = SimpleNamespace(template=SimpleNamespace(encode_oneturn=lambda *args: ([1, 2, 3], [])),
                                 tokenizer=object())
        backend = model_eval.FactoryBackend.__new__(model_eval.FactoryBackend)
        backend.model = SimpleNamespace(engine=engine, chat=Mock(return_value=[response]))
        result = backend.chat('user', 'system')
        self.assertEqual(result, {'text': '{}', 'finish_reason': 'stop', 'prompt_tokens': 3, 'output_tokens': 2})
        self.assertEqual(backend.model.chat.call_args.kwargs['max_new_tokens'], 256)
        self.assertFalse(backend.model.chat.call_args.kwargs['do_sample'])
