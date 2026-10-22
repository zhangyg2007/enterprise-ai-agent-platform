import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from enterprise_ai.finetune_data import generate_bundle, validate_bundle
try:
    from enterprise_ai import training
except ImportError:
    training = None


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'data/samples/m04-intents.jsonl'


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(training, 'M06 preparation module is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'bundle'
        generate_bundle(self.data, 42, BASELINE)
        self.model = self.root / 'model'
        self.model.mkdir()
        self.put(self.model / 'config.json', {'model_type': 'qwen3'})
        self.put(self.model / 'tokenizer.json', {'model': {}})
        self.put(self.model / 'tokenizer_config.json', {'tokenizer_class': 'Qwen2Tokenizer'})
        self.put(self.model / 'model.safetensors.index.json',
                 {'weight_map': {'layer': 'model-00001-of-00001.safetensors'}})
        (self.model / 'model-00001-of-00001.safetensors').write_bytes(b'fixture-not-real-weights')
        self.run = self.root / 'run'

    def put(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def prepare(self, profile='smoke'):
        return training.prepare_run(self.data, self.model, self.run, profile, BASELINE)

    def test_smoke_copies_only_training_validation_with_original_columns(self):
        manifest = self.prepare()
        self.assertEqual(manifest['counts'], {'train': 64, 'validation': 32})
        self.assertEqual(training.verify_prepared(self.run), manifest)
        for split, size in [('train', 64), ('validation', 32)]:
            original = json.loads((self.data / f'{split}-alpaca.json').read_text(encoding='utf-8'))
            copied = json.loads((self.run / f'data/{split}.json').read_text(encoding='utf-8'))
            self.assertEqual(copied, original[:size])
        registry = json.loads((self.run / 'data/dataset_info.json').read_text())
        self.assertEqual(set(registry), {'m06_train', 'm06_validation'})
        self.assertEqual(registry['m06_train']['columns'],
                         {'prompt': 'instruction', 'query': 'input', 'response': 'output', 'system': 'system'})
        self.assertEqual({p.name for p in (self.run / 'data').iterdir()},
                         {'train.json', 'validation.json', 'dataset_info.json'})
        self.assertFalse((self.run / 'adapter').exists())

    def test_sft_uses_all_train_validation_and_absolute_paths(self):
        manifest = self.prepare('sft')
        self.assertEqual(manifest['counts'], {'train': 336, 'validation': 96})
        config = json.loads((self.run / 'train.yaml').read_text())
        self.assertEqual(config['model_name_or_path'], str(self.model.resolve()))
        self.assertEqual(config['dataset_dir'], str((self.run / 'data').resolve()))
        self.assertEqual(config['output_dir'], str((self.run / 'adapter').resolve()))
        self.assertFalse(config['overwrite_output_dir'])

    def test_reusing_run_directory_never_overwrites(self):
        self.prepare()
        before = (self.run / 'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((self.run / 'manifest.json').read_bytes(), before)

    def test_bad_source_bundle_and_profile_fail_without_creating_run(self):
        with self.assertRaises(ValueError):
            self.prepare('../smoke')
        (self.data / 'train-alpaca.json').write_text('[]')
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.run.exists())

    def test_validation_and_copy_use_same_source_snapshot(self):
        original = (self.data / 'train-alpaca.json').read_bytes()
        def validate_then_change_source(snapshot, baseline):
            result = validate_bundle(snapshot, baseline)
            (self.data / 'train-alpaca.json').write_text('[]')
            return result
        with patch.object(training, 'validate_bundle', side_effect=validate_then_change_source):
            self.prepare('sft')
        self.assertEqual(json.loads((self.run / 'data/train.json').read_bytes()), json.loads(original))

    def test_gguf_and_wrong_model_type_rejected(self):
        (self.model / 'bad.gguf').write_bytes(b'GGUF')
        with self.assertRaises(ValueError):
            self.prepare()
        (self.model / 'bad.gguf').unlink()
        self.put(self.model / 'config.json', {'model_type': 'qwen2'})
        with self.assertRaises(ValueError):
            self.prepare()

    def test_missing_or_empty_shard_rejected(self):
        shard = self.model / 'model-00001-of-00001.safetensors'
        shard.write_bytes(b'')
        with self.assertRaises(ValueError):
            self.prepare()
        shard.unlink()
        with self.assertRaises((ValueError, OSError)):
            self.prepare()

    def test_index_traversal_absolute_and_backslash_escape_rejected(self):
        outside = self.root / 'outside.safetensors'
        outside.write_bytes(b'not weights')
        for filename in ('../outside.safetensors', '..\\outside.safetensors', str(outside), 'C:/outside.safetensors'):
            with self.subTest(filename=filename):
                self.put(self.model / 'model.safetensors.index.json', {'weight_map': {'layer': filename}})
                with self.assertRaises(ValueError):
                    self.prepare()

    def test_empty_weight_map_and_missing_tokenizer_rejected(self):
        self.put(self.model / 'model.safetensors.index.json', {'weight_map': {}})
        with self.assertRaises(ValueError):
            self.prepare()
        self.put(self.model / 'model.safetensors.index.json',
                 {'weight_map': {'layer': 'model-00001-of-00001.safetensors'}})
        (self.model / 'tokenizer.json').unlink()
        with self.assertRaises((ValueError, OSError)):
            self.prepare()

    def test_modified_artifact_or_model_config_rejected(self):
        self.prepare()
        config = self.model / 'config.json'
        original = config.read_bytes()
        self.put(config, {'model_type': 'qwen3', 'changed': True})
        with self.assertRaises(ValueError):
            training.verify_prepared(self.run)
        config.write_bytes(original)
        (self.run / 'data/train.json').write_text('[]')
        with self.assertRaises(ValueError):
            training.verify_prepared(self.run)

    def test_rehashed_registry_test_selection_and_manifest_path_escape_rejected(self):
        manifest = self.prepare()
        registry_path = self.run / 'data/dataset_info.json'
        registry = json.loads(registry_path.read_text())
        registry['m06_train']['file_name'] = '../../bundle/test-alpaca.json'
        self.put(registry_path, registry)
        manifest['artifacts']['data/dataset_info.json'] = hashlib.sha256(registry_path.read_bytes()).hexdigest()
        self.put(self.run / 'manifest.json', manifest)
        with self.assertRaises(ValueError):
            training.verify_prepared(self.run)
        manifest['artifacts']['../outside'] = '0' * 64
        self.put(self.run / 'manifest.json', manifest)
        with self.assertRaises(ValueError):
            training.verify_prepared(self.run)

    def test_rehashed_config_path_change_and_wrong_count_rejected(self):
        manifest = self.prepare()
        path = self.run / 'train.yaml'
        config = json.loads(path.read_text())
        config['output_dir'] = str(self.root / 'another-run')
        self.put(path, config)
        manifest['artifacts']['train.yaml'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.put(self.run / 'manifest.json', manifest)
        with self.assertRaises(ValueError):
            training.verify_prepared(self.run)
        manifest['counts']['train'] = 63
        self.put(self.run / 'manifest.json', manifest)
        with self.assertRaises(ValueError):
            training.verify_prepared(self.run)

    def test_failed_write_leaves_preparing_and_is_not_verifiable(self):
        original = training.write_new
        def failing_write(path, raw):
            if Path(path) == self.run / 'data/train.json':
                raise OSError('simulated full disk')
            return original(path, raw)
        with patch.object(training, 'write_new', side_effect=failing_write):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(json.loads((self.run / 'manifest.json').read_text())['status'], 'preparing')
        with self.assertRaises(ValueError):
            training.verify_prepared(self.run)

    def test_training_outputs_are_allowed_after_preparation(self):
        manifest = self.prepare()
        (self.run / 'adapter').mkdir()
        (self.run / 'adapter/trainer_state.json').write_text('{}')
        self.assertEqual(training.verify_prepared(self.run), manifest)


if __name__ == '__main__':
    unittest.main()
