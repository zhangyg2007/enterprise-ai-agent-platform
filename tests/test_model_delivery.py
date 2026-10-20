import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from enterprise_ai.finetune_data import generate_bundle, validate_bundle
from enterprise_ai.training import prepare_run
try:
    from enterprise_ai import model_delivery as delivery
except ImportError:
    delivery = None

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'data/samples/m04-intents.jsonl'


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(delivery, 'M07 delivery module is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data, self.model, self.run, self.release = [self.root / n for n in ('data', 'model', 'run', 'release')]
        generate_bundle(self.data, 42, BASELINE)
        self.model.mkdir()
        self.put(self.model / 'config.json', {'model_type': 'qwen3'})
        self.put(self.model / 'tokenizer.json', {'model': {}})
        self.put(self.model / 'tokenizer_config.json', {'tokenizer_class': 'Qwen2Tokenizer'})
        self.put(self.model / 'model.safetensors.index.json', {'weight_map': {'layer': 'model-00001-of-00001.safetensors'}})
        (self.model / 'model-00001-of-00001.safetensors').write_bytes(b'fixture-not-real-weights')
        prepare_run(self.data, self.model, self.run, 'sft', BASELINE)
        self.adapter = self.run / 'adapter'
        self.adapter.mkdir()
        self.put(self.adapter / 'adapter_config.json', {'peft_type': 'LORA', 'base_model_name_or_path': str(self.model), 'r': 8})
        (self.adapter / 'adapter_model.safetensors').write_bytes(b'fixture-not-real-adapter')

    def put(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def prepare(self):
        return delivery.prepare_release(self.run, self.data, self.adapter, self.release, BASELINE)

    def config(self, name):
        return json.loads((self.release / name).read_bytes())

    def test_release_contains_only_validation_test_and_verified_fingerprints(self):
        manifest = self.prepare()
        self.assertEqual(manifest['schema_version'], 'm07-v1')
        self.assertEqual(manifest['status'], 'prepared')
        self.assertEqual(manifest['counts'], {'validation': 96, 'test': 48})
        self.assertEqual(delivery.verify_release(self.release), manifest)
        for split, count in [('validation', 96), ('test', 48)]:
            cases = [json.loads(line) for line in (self.release / f'cases-{split}.jsonl').read_bytes().splitlines()]
            samples = [json.loads(line) for line in (self.data / 'samples.jsonl').read_bytes().splitlines()]
            expected = [{'id': s['sample_id'], 'category': s['category'], 'input': s['prompt'], 'expected': s['answer']}
                        for s in samples if s['split'] == split]
            self.assertEqual(cases, expected)
            self.assertEqual(len(cases), count)
        self.assertEqual(set(manifest['artifacts']), {'cases-validation.jsonl', 'cases-test.jsonl', 'base.yaml', 'adapter.yaml', 'adapter-bf16.yaml', 'merged.yaml', 'export.yaml'})
        shard = self.model / 'model-00001-of-00001.safetensors'
        self.assertEqual(manifest['assets']['base']['files'][shard.name], {'size': shard.stat().st_size, 'sha256': hashlib.sha256(shard.read_bytes()).hexdigest()})
        self.assertFalse((self.release / 'merged-model').exists())

    def test_precision_pairs_and_cpu_export_are_safe_lf_configurations(self):
        manifest = self.prepare()
        base, adapter, bf16, merged, export = [self.config(n + '.yaml') for n in ('base', 'adapter', 'adapter-bf16', 'merged', 'export')]
        self.assertEqual({k: v for k, v in adapter.items() if k != 'adapter_name_or_path'}, base)
        self.assertEqual(adapter['adapter_name_or_path'], str(self.adapter))
        self.assertEqual(base['quantization_bit'], 4)
        self.assertEqual(base['quantization_method'], 'bitsandbytes')
        self.assertEqual(base['quantization_type'], 'nf4')
        self.assertTrue(base['double_quantization'])
        for config in (base, adapter, bf16, merged):
            self.assertEqual(config['stage'], 'sft')
            self.assertEqual(config['finetuning_type'], 'lora')
            self.assertEqual(config['infer_backend'], 'huggingface')
            self.assertEqual(config['template'], 'qwen3_nothink')
            self.assertEqual(config['infer_dtype'], 'bfloat16')
            self.assertEqual(config['flash_attn'], 'sdpa')
            self.assertEqual(config['cutoff_len'], 2048)
            self.assertFalse(config['trust_remote_code'])
            self.assertFalse(config['do_sample'])
            self.assertEqual(config['max_new_tokens'], 256)
        for config in (bf16, merged, export):
            self.assertFalse(any('quantization' in key for key in config))
        self.assertEqual(manifest['generation'], {'do_sample': False, 'max_new_tokens': 256, 'num_return_sequences': 1, 'repetition_penalty': 1.0})
        self.assertEqual(manifest['seed'], 42)
        self.assertEqual(export, {'model_name_or_path': str(self.model), 'adapter_name_or_path': str(self.adapter), 'template': 'qwen3_nothink', 'finetuning_type': 'lora', 'stage': 'sft', 'trust_remote_code': False, 'export_dir': str(self.release / 'merged-model'), 'export_device': 'cpu', 'infer_dtype': 'bfloat16', 'export_size': 2, 'export_legacy_format': False})

    def test_source_bundle_must_match_sft_run(self):
        other = self.root / 'other'
        generate_bundle(other, 7, BASELINE)
        self.data = other
        with self.assertRaisesRegex(ValueError, 'source|bundle'):
            self.prepare()
        self.assertFalse(self.release.exists())

    def test_per_call_sequence_count_is_not_an_unsupported_lf_config_field(self):
        self.prepare()
        for name in ('base', 'adapter', 'adapter-bf16', 'merged'):
            self.assertNotIn('num_return_sequences', self.config(name + '.yaml'))
        self.assertEqual(self.config('manifest.json')['generation']['num_return_sequences'], 1)

    def test_smoke_run_rejected(self):
        self.run = self.root / 'smoke'
        prepare_run(self.data, self.model, self.run, 'smoke', BASELINE)
        with self.assertRaisesRegex(ValueError, 'sft|smoke'):
            self.prepare()

    def test_checkpoint_adapter_allowed_but_unrelated_directory_rejected(self):
        checkpoint = self.adapter / 'checkpoint-25'
        checkpoint.mkdir()
        for path in self.adapter.iterdir():
            if path.is_file():
                (checkpoint / path.name).write_bytes(path.read_bytes())
        self.adapter = checkpoint
        self.prepare()
        self.release = self.root / 'other-release'
        unrelated = self.root / 'unrelated'
        unrelated.mkdir()
        for path in checkpoint.iterdir():
            (unrelated / path.name).write_bytes(path.read_bytes())
        self.adapter = unrelated
        with self.assertRaisesRegex(ValueError, 'adapter|run'):
            self.prepare()

    def test_wrong_adapter_type_base_or_empty_weights_rejected(self):
        for config in ({'peft_type': 'IA3', 'base_model_name_or_path': str(self.model)}, {'peft_type': 'LORA', 'base_model_name_or_path': '/different-model'}):
            self.put(self.adapter / 'adapter_config.json', config)
            with self.assertRaises(ValueError):
                self.prepare()
        self.put(self.adapter / 'adapter_config.json', {'peft_type': 'LORA', 'base_model_name_or_path': str(self.model)})
        (self.adapter / 'adapter_model.safetensors').write_bytes(b'')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_assets_changes_are_detected_but_unrelated_outputs_allowed(self):
        manifest = self.prepare()
        (self.release / 'evaluation').mkdir()
        (self.release / 'merged-model').mkdir()
        self.assertEqual(delivery.verify_release(self.release), manifest)
        for target in (self.model / 'tokenizer.json', self.model / 'model-00001-of-00001.safetensors', self.adapter / 'adapter_model.safetensors'):
            original = target.read_bytes()
            target.write_bytes(original + b' ')
            with self.assertRaises(ValueError):
                delivery.verify_release(self.release)
            self.assertEqual(delivery.verify_release(self.release, check_assets=False), manifest)
            target.write_bytes(original)

    def test_single_file_merged_checkpoint_can_be_fingerprinted(self):
        (self.model / 'model.safetensors.index.json').unlink()
        (self.model / 'model-00001-of-00001.safetensors').rename(self.model / 'model.safetensors')
        files = delivery.fingerprint_model(self.model)
        self.assertIn('model.safetensors', files)

    def test_ambiguous_single_file_and_sharded_layout_is_rejected(self):
        self.prepare()
        (self.model / 'model.safetensors').write_bytes(b'untracked-preferred-checkpoint')
        with self.assertRaises(ValueError):
            delivery.fingerprint_model(self.model)
        with self.assertRaises(ValueError):
            delivery.verify_release(self.release)

    def test_optional_tokenizer_assets_are_fingerprinted(self):
        for name in ('generation_config.json', 'vocab.json', 'merges.txt', 'special_tokens_map.json'):
            (self.model / name).write_bytes(b'{}')
        files = delivery.fingerprint_model(self.model)
        self.assertTrue({'generation_config.json', 'vocab.json', 'merges.txt', 'special_tokens_map.json'} <= set(files))

    def test_unsafe_shard_paths_rejected(self):
        for name in ('../outside.safetensors', 'C:/outside.safetensors', '..\\outside.safetensors'):
            self.put(self.model / 'model.safetensors.index.json', {'weight_map': {'layer': name}})
            with self.assertRaises(ValueError):
                delivery.fingerprint_model(self.model)

    def test_artifact_tampering_and_rehashed_config_changes_rejected(self):
        manifest = self.prepare()
        path = self.release / 'base.yaml'
        config = self.config('base.yaml')
        config['quantization_bit'] = 8
        self.put(path, config)
        with self.assertRaises(ValueError):
            delivery.read_artifact(self.release, manifest, 'base.yaml')
        manifest['artifacts']['base.yaml'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.put(self.release / 'manifest.json', manifest)
        with self.assertRaises(ValueError):
            delivery.verify_release(self.release)
        with self.assertRaises(ValueError):
            delivery.read_artifact(self.release, manifest, '../outside')

    def test_bundle_validation_and_export_share_private_snapshot(self):
        original = (self.data / 'test-cases.jsonl').read_bytes()
        def validate_then_change(snapshot, baseline):
            result = validate_bundle(snapshot, baseline)
            (self.data / 'samples.jsonl').write_bytes(b'bad')
            return result
        with patch.object(delivery, 'validate_bundle', side_effect=validate_then_change):
            self.prepare()
        self.assertEqual((self.release / 'cases-test.jsonl').read_bytes(), original)

    def test_existing_release_is_never_overwritten(self):
        self.prepare()
        before = (self.release / 'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((self.release / 'manifest.json').read_bytes(), before)

    def test_failed_write_retains_preparing_state(self):
        original = delivery.write_new
        def failing_write(path, raw):
            if path == self.release / 'base.yaml':
                raise OSError('simulated disk full')
            return original(path, raw)
        with patch.object(delivery, 'write_new', side_effect=failing_write):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(self.config('manifest.json')['status'], 'preparing')
        with self.assertRaises(ValueError):
            delivery.verify_release(self.release)

    def test_export_reserves_new_directory_and_uses_frozen_cpu_config(self):
        import subprocess
        self.prepare()
        def exporter(command, **kwargs):
            self.assertEqual(command[:2], ['llamafactory-cli', 'export'])
            config = json.loads(Path(command[2]).read_bytes())
            self.assertEqual(config['export_device'], 'cpu')
            self.assertEqual(kwargs['env']['CUDA_VISIBLE_DEVICES'], '')
            import shutil
            for path in self.model.iterdir():
                shutil.copyfile(path, self.release / 'merged-model' / path.name)
            return subprocess.CompletedProcess(command, 0)
        with patch.object(delivery.subprocess, 'run', side_effect=exporter) as process:
            result = delivery.export_release(self.release)
            self.assertEqual(result['status'], 'exported')
            self.assertEqual(process.call_count, 1)
            with self.assertRaises(FileExistsError):
                delivery.export_release(self.release)
            self.assertEqual(process.call_count, 1)

    def test_export_failure_retains_state_and_does_not_allow_retry_in_place(self):
        self.prepare()
        with patch.object(delivery.subprocess, 'run', side_effect=OSError('fixture missing executable')):
            self.assertEqual(delivery.export_release(self.release)['status'], 'failed')
        self.assertEqual(self.config('export-state.json')['status'], 'failed')
        with self.assertRaises(FileExistsError):
            delivery.export_release(self.release)


if __name__ == '__main__':
    unittest.main()
