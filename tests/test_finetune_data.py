import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from enterprise_ai.baseline import load_cases
from enterprise_ai.finetune_data import build_samples, validate_samples, generate_bundle, validate_bundle

BASELINE = Path(__file__).resolve().parents[1] / 'data/samples/m04-intents.jsonl'


class SampleTests(unittest.TestCase):
    def test_counts_reproducibility_and_different_seed(self):
        samples = build_samples(42)
        self.assertEqual(len(samples), 480)
        self.assertEqual(samples, build_samples(42))
        self.assertNotEqual(samples, build_samples(43))
        report = validate_samples(samples, load_cases(BASELINE))
        self.assertEqual(report['counts'], {'train': 336, 'validation': 96, 'test': 48})
        self.assertEqual(report['groups'], {'train': 21, 'validation': 6, 'test': 3})
        self.assertEqual(report['categories_per_split']['test']['crm_query'], 6)

    def test_seed_rejects_bool_negative_and_huge(self):
        for value in (True, -1, 2**32, '42'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_samples(value)

    def test_group_leakage_rejected(self):
        samples = build_samples(42)
        samples[0]['split'] = 'test' if samples[0]['split'] != 'test' else 'train'
        with self.assertRaises(ValueError):
            validate_samples(samples, load_cases(BASELINE))

    def test_entity_leakage_rejected(self):
        samples = build_samples(42)
        other = next(s for s in samples if s['split'] != samples[0]['split'])
        samples[0]['entities'] = other['entities']
        with self.assertRaises(ValueError):
            validate_samples(samples, load_cases(BASELINE))

    def test_wrong_label_and_bool_id_rejected(self):
        for answer in ({'intent': 'inventory', 'customer_id': None, 'product_id': 9, 'action': 'query'},
                       {'intent': 'customer_summary', 'customer_id': True, 'product_id': None, 'action': 'query'}):
            samples = build_samples(42)
            samples[0]['answer'] = answer
            with self.assertRaises(ValueError):
                validate_samples(samples, load_cases(BASELINE))

    def test_duplicate_and_cross_split_template_rejected(self):
        samples = build_samples(42)
        samples[-1] = copy.deepcopy(samples[0])
        with self.assertRaises(ValueError):
            validate_samples(samples, load_cases(BASELINE))
        samples = build_samples(42)
        other = next(s for s in samples if s['split'] != samples[0]['split'])
        other['prompt'] = samples[0]['prompt']
        with self.assertRaises(ValueError):
            validate_samples(samples, load_cases(BASELINE))

    def test_m04_question_or_entity_overlap_rejected(self):
        samples = build_samples(42)
        baseline = [{'id':'protected','category':'crm','input':samples[0]['prompt'],
                     'expected':samples[0]['answer']}]
        with self.assertRaises(ValueError):
            validate_samples(samples, baseline)
        baseline[0]['input'] = 'Different wording'
        with self.assertRaises(ValueError):
            validate_samples(samples, baseline)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def generate(self, name='bundle'):
        root = self.root / name
        report = generate_bundle(root, 42, BASELINE)
        return root, report

    def test_bundle_round_trip_formats_and_registration(self):
        root, report = self.generate()
        self.assertEqual(validate_bundle(root, BASELINE), report)
        self.assertEqual(len(load_cases(root / 'test-cases.jsonl')), 48)
        info = json.loads((root / 'dataset_info.json').read_text(encoding='utf-8'))
        self.assertEqual(set(info), {'m05_train_alpaca','m05_validation_alpaca',
                                     'm05_train_sharegpt','m05_validation_sharegpt'})
        alpaca = json.loads((root / 'train-alpaca.json').read_text(encoding='utf-8'))
        share = json.loads((root / 'train-sharegpt.json').read_text(encoding='utf-8'))
        self.assertEqual(len(alpaca), 336)
        self.assertEqual(alpaca[0]['instruction'], share[0]['conversations'][0]['value'])
        self.assertEqual(alpaca[0]['output'], share[0]['conversations'][1]['value'])
        self.assertEqual(alpaca[0]['input'], '')
        self.assertEqual(share[0]['conversations'][1]['from'], 'gpt')
        self.assertEqual(json.loads((root / 'manifest.json').read_text())['status'], 'complete')

    def test_deterministic_bundle_bytes_and_no_overwrite(self):
        first, _ = self.generate('first')
        second, _ = self.generate('second')
        for file in first.iterdir():
            self.assertEqual(file.read_bytes(), (second/file.name).read_bytes(), file.name)
        with self.assertRaises(FileExistsError):
            generate_bundle(first, 42, BASELINE)

    def test_tampered_export_is_rejected(self):
        root, _ = self.generate()
        (root/'train-alpaca.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            validate_bundle(root, BASELINE)

    def test_test_registration_and_path_escape_rejected_even_with_updated_hash(self):
        from enterprise_ai.finetune_data import sha256
        root, _ = self.generate()
        manifest = json.loads((root/'manifest.json').read_text())
        info = json.loads((root/'dataset_info.json').read_text())
        info['m05_train_alpaca']['file_name'] = 'test-alpaca.json'
        raw = json.dumps(info).encode()
        (root/'dataset_info.json').write_bytes(raw)
        manifest['artifacts']['dataset_info.json'] = sha256(raw)
        (root/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            validate_bundle(root, BASELINE)
        manifest['artifacts']['../outside.json'] = '0'*64
        (root/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            validate_bundle(root, BASELINE)

    def test_baseline_change_invalidates_protection_evidence(self):
        root, _ = self.generate()
        modified = self.root/'baseline.jsonl'
        modified.write_bytes(BASELINE.read_bytes()+b'\n')
        with self.assertRaises(ValueError):
            validate_bundle(root, modified)

    def test_partial_generation_never_becomes_complete(self):
        import enterprise_ai.finetune_data as module
        original = module.write_new
        def fail_export(path, data):
            if path.name == 'train-alpaca.json':
                raise OSError('simulated storage failure')
            return original(path, data)
        with patch.object(module, 'write_new', side_effect=fail_export):
            with self.assertRaises(OSError):
                self.generate()
        manifest = json.loads((self.root/'bundle/manifest.json').read_text())
        self.assertEqual(manifest['status'], 'building')
        with self.assertRaises(ValueError):
            validate_bundle(self.root/'bundle', BASELINE)


if __name__ == '__main__':
    unittest.main()
