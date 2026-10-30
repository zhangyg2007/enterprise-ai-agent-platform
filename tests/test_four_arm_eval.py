import json
from pathlib import Path
import tempfile
import unittest

from enterprise_ai.four_arm_eval import collect, fixture_backend, validate_snapshot


class FourArmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = {'kind': 'synthetic-fixture', 'cases': [
            {'id': 'a', 'question': '报价制度是什么？', 'tool_snapshot': {'amount': '190.00'},
             'citations': [{'document_id': 'crm-0001', 'version': 1, 'quote': '需经理审批'}]},
            {'id': 'b', 'question': '余额是多少？', 'tool_snapshot': {'days': 15},
             'citations': [{'document_id': 'hcm-0001', 'version': 1, 'quote': '申请需要审批'}]}]}

    def test_four_arms_reuse_cases_tools_and_rag_snapshot(self):
        report = collect(self.data, self.root/'run', fixture_backend)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['kind'], 'synthetic-fixture')
        records = json.loads((self.root/'run/records.json').read_text())
        self.assertEqual(len(records), 8)
        for case in self.data['cases']:
            rows = [r for r in records if r['case_id'] == case['id']]
            self.assertEqual({r['arm'] for r in rows}, {'base', 'base_rag', 'adapter', 'adapter_rag'})
            self.assertEqual(len({r['tool_sha256'] for r in rows}), 1)
            self.assertEqual(rows[1]['evidence_sha256'], rows[3]['evidence_sha256'])
            self.assertIsNone(rows[0]['evidence_sha256'])
            self.assertIsNone(rows[2]['evidence_sha256'])
        self.assertEqual({v['total'] for v in report['arms'].values()}, {2})
        with self.assertRaises(FileExistsError):
            collect(self.data, self.root/'run', fixture_backend)

    def test_interruption_preserves_completed_results_without_complete_report(self):
        class Interrupted:
            calls = 0
            def chat(self, text, system):
                self.calls += 1
                if self.calls == 3: raise KeyboardInterrupt()
                return {'text': 'done', 'finish_reason': 'stop'}
        with self.assertRaises(KeyboardInterrupt):
            collect(self.data, self.root/'interrupted', lambda variant: Interrupted())
        output = self.root/'interrupted'
        rows = [json.loads(line) for line in (output/'records.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertFalse((output/'report.json').exists())

    def test_failed_calls_remain_in_denominator_without_exception_leak(self):
        def backend(variant):
            inner = fixture_backend(variant)
            class Failing:
                def chat(self, text, system):
                    if '余额' in text: raise TimeoutError('SECRET')
                    return inner.chat(text, system)
            return Failing()
        report = collect(self.data, self.root/'run', backend)
        self.assertEqual(report['status'], 'complete_with_failures')
        for arm in report['arms'].values():
            self.assertEqual((arm['total'], arm['succeeded'], arm['failed']), (2, 1, 1))
        raw = (self.root/'run/records.json').read_text()
        self.assertNotIn('SECRET', raw)
        self.assertEqual(len(json.loads(raw)), 8)

    def test_truncated_response_is_not_counted_as_success_and_raw_is_retained(self):
        class Truncated:
            def chat(self, text, system):
                return {'text': 'partial answer', 'finish_reason': 'length', 'prompt_tokens': 2, 'output_tokens': 256}
        report = collect(self.data, self.root/'truncated', lambda variant: Truncated())
        self.assertEqual(report['status'], 'complete_with_failures')
        self.assertEqual({v['failed'] for v in report['arms'].values()}, {2})
        records = json.loads((self.root/'truncated/records.json').read_text())
        self.assertEqual(records[0]['answer'], 'partial answer')
        self.assertEqual(records[0]['error'], 'truncated')

    def test_duplicate_case_bad_evidence_and_unexpected_fields_rejected(self):
        duplicated = {'kind': 'synthetic-fixture', 'cases': self.data['cases']*2}
        with self.assertRaises(ValueError): validate_snapshot(duplicated)
        invalid = json.loads(json.dumps(self.data))
        invalid['cases'][0]['citations'][0]['version'] = True
        with self.assertRaises(ValueError): validate_snapshot(invalid)

    def test_empty_evidence_is_kept_as_an_insufficient_evidence_case(self):
        self.data['cases'][0]['citations'] = []
        report = collect(self.data, self.root/'run', fixture_backend)
        self.assertEqual(report['arms']['base_rag']['total'], 2)
        records = json.loads((self.root/'run/records.json').read_text())
        record = next(r for r in records if r['case_id'] == 'a' and r['arm'] == 'base_rag')
        self.assertEqual(record['evidence_status'], 'insufficient')

    def test_changed_model_identity_invalidates_completed_run(self):
        report = collect(self.data, self.root/'run', fixture_backend, identity_check=lambda: False)
        self.assertEqual(report['status'], 'invalid_asset_change')
        self.assertFalse(report['identity_stable'])
        self.assertEqual({v['total'] for v in report['arms'].values()}, {2})
        invalid = json.loads(json.dumps(self.data))
        invalid['cases'][0]['actor'] = 'manager'
        with self.assertRaises(ValueError): validate_snapshot(invalid)
