"""Offline checks: no GPU, tokenizer downloads, or training dependencies."""
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


class TrainingChecksTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('enterprise_ai.training_checks'),
                             'training checks module must exist')
        self.checks = importlib.import_module('enterprise_ai.training_checks')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'report.json'

    def fixture(self, rows=None, cutoff=2048):
        run = self.root / 'run'
        (run / 'data').mkdir(parents=True)
        rows = rows or [{'instruction': 'Ask', 'input': 'Input', 'output': 'Answer',
                         'system': 'System'}]
        for split in ('train', 'validation'):
            (run / 'data' / (split + '.json')).write_text(json.dumps(rows), encoding='utf-8')
        # A poison file must never be consumed, even if present.
        (run / 'data/test.json').write_text('NOT JSON', encoding='utf-8')
        (run / 'train.yaml').write_text(json.dumps({'cutoff_len': cutoff,
            'template': 'qwen3_nothink', 'model_name_or_path': str(self.root / 'model')}),
            encoding='utf-8')
        return run, {'model': {'path': str(self.root / 'model')},
                     'counts': {'train': len(rows), 'validation': len(rows)},
                     'artifacts': {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                         for name in ('train.yaml', 'data/train.json', 'data/validation.json')}}

    def fake_template(self, efficient_eos=False, zero_answer=False):
        def encode(tokenizer, messages, system=None, tools=None):
            self.assertEqual([m['role'] for m in messages], ['user', 'assistant'])
            # Character counts form independently hand-computable token lengths.
            return list(system + messages[0]['content']), ([] if zero_answer else
                list(messages[1]['content']) + ([] if efficient_eos else [999]))
        return SimpleNamespace(efficient_eos=efficient_eos, encode_oneturn=encode)

    def run_lengths(self, run, manifest, template=None):
        tokenizer = SimpleNamespace(eos_token_id=999, name_or_path='local-tokenizer')
        with patch.object(self.checks, '_verify_prepared', return_value=manifest), \
             patch.object(self.checks, '_load_tokenizer_template',
                          return_value=(tokenizer, template or self.fake_template())):
            return self.checks.token_lengths(run, self.output)

    def test_lengths_include_system_instruction_input_answer_and_template_eos(self):
        run, manifest = self.fixture(cutoff=22)
        report = self.run_lengths(run, manifest)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['splits']['train']['min'], 22)
        self.assertEqual(report['splits']['validation']['max'], 22)
        self.assertEqual(report['splits']['train']['count'], 1)
        self.assertEqual(json.loads(self.output.read_text())['status'], 'passed')

    def test_over_cutoff_and_empty_answers_record_zero_based_indices(self):
        rows = [{'instruction': 'Ask', 'input': 'Input', 'output': 'Answer', 'system': 'System'},
                {'instruction': 'Q', 'input': '', 'output': ' ', 'system': ''}]
        run, manifest = self.fixture(rows, cutoff=20)
        report = self.run_lengths(run, manifest)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['splits']['train']['over_cutoff_indices'], [0])
        self.assertEqual(report['splits']['train']['empty_answer_indices'], [1])

    def test_efficient_eos_added_once_and_empty_encoded_answer_rejected(self):
        run, manifest = self.fixture(cutoff=20)
        report = self.run_lengths(run, manifest, self.fake_template(efficient_eos=True))
        self.assertEqual(report['splits']['train']['max'], 22)
        self.assertEqual(report['status'], 'failed')
        self.output = self.root / 'zero.json'
        report = self.run_lengths(run, manifest, self.fake_template(zero_answer=True))
        self.assertEqual(report['splits']['train']['empty_answer_indices'], [0])

    def test_invalid_prepared_run_is_reported_before_loading_dependencies(self):
        with patch.object(self.checks, '_verify_prepared', side_effect=ValueError('secret-key')), \
             patch.object(self.checks, '_load_tokenizer_template') as loader:
            report = self.checks.token_lengths(self.root, self.output)
        self.assertEqual(report['status'], 'failed')
        loader.assert_not_called()
        self.assertNotIn('secret-key', self.output.read_text())

    def test_no_overwrite_or_gpu_work_when_report_exists(self):
        self.output.write_text('KEEP', encoding='utf-8')
        with patch.object(self.checks, '_gpu_probe') as gpu:
            with self.assertRaises(FileExistsError):
                self.checks.preflight(self.root, self.output)
        gpu.assert_not_called()
        self.assertEqual(self.output.read_text(), 'KEEP')

    def preflight_patches(self, versions=None, head=None, gpu=None):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(self.checks.platform, 'python_version', return_value='3.11.9'))
        stack.enter_context(patch.object(self.checks.metadata, 'version',
            side_effect=lambda name: (versions or self.checks.CORE_VERSIONS)[name]))
        stack.enter_context(patch.object(self.checks, '_factory_revision',
            return_value=head or self.checks.FACTORY_REVISION))
        stack.enter_context(patch.object(self.checks.subprocess, 'run',
            return_value=SimpleNamespace(returncode=0)))
        stack.enter_context(patch.dict(sys.modules, {'llamafactory': SimpleNamespace(
            __file__=str(self.root / 'src/llamafactory/__init__.py'), __version__='0.9.4')}))
        stack.enter_context(patch.object(self.checks, '_gpu_probe', return_value=gpu or
            {'cuda_available': True, 'bf16_supported': True, 'cuda_version': '12.4',
             'nf4_forward_backward': True, 'devices': [{'index': 0, 'name': 'test GPU'}]}))
        return stack

    def test_preflight_success_reports_actual_versions_and_small_probe_scope(self):
        with self.preflight_patches():
            report = self.checks.preflight(self.root, self.output)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['packages']['bitsandbytes']['actual'], '0.46.1')
        self.assertIn('4B', report['scope'])

    def test_preflight_rejects_wrong_revision_package_cuda_and_bf16(self):
        versions = dict(self.checks.CORE_VERSIONS, torch='2.6.0+cpu')
        with self.preflight_patches(versions=versions, head='a' * 40,
                gpu={'cuda_available': False, 'bf16_supported': False,
                     'nf4_forward_backward': False, 'cuda_version': None, 'devices': []}):
            report = self.checks.preflight(self.root, self.output)
        self.assertEqual(report['status'], 'failed')
        self.assertTrue({'factory_revision', 'package:torch', 'cuda_available',
                         'bf16_supported', 'nf4_forward_backward'}.issubset(report['errors']))

    def test_missing_dependencies_keep_failure_report_without_exception_secrets(self):
        with self.preflight_patches(), \
             patch.object(self.checks, '_gpu_probe', side_effect=ImportError('hf_SECRET')), \
             patch.object(self.checks.metadata, 'version', side_effect=ImportError('hf_SECRET')):
            report = self.checks.preflight(self.root, self.output)
        self.assertEqual(report['status'], 'failed')
        self.assertNotIn('hf_SECRET', self.output.read_text())

    def test_cli_failed_checks_exit_nonzero_and_hide_exception_details(self):
        with patch.object(self.checks, 'preflight', return_value={'status': 'failed'}), \
             contextlib.redirect_stdout(io.StringIO()):
            result = self.checks.main(['preflight', '--factory-dir', str(self.root),
                                       '--output', str(self.output)])
        self.assertEqual(result, 1)
        with patch.object(self.checks, 'token_lengths', side_effect=ValueError('secret')), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = self.checks.main(['token-lengths', '--run-dir', str(self.root),
                                       '--output', str(self.output)])
        self.assertEqual(result, 1)
        self.assertNotIn('secret', stderr.getvalue())

    def test_cpu_and_non_native_bf16_skip_quantization(self):
        torch = MagicMock()
        torch.version.cuda = '12.4'
        torch.cuda.is_available.return_value = False
        with patch.dict(sys.modules, {'torch': torch}):
            report = self.checks._gpu_probe()
        self.assertFalse(report['cuda_available'])
        torch.randn.assert_not_called()
        torch.cuda.is_available.return_value = True
        torch.cuda.get_device_properties.return_value = SimpleNamespace(name='GPU', total_memory=100)
        torch.cuda.get_device_capability.return_value = (7, 5)
        torch.cuda.is_bf16_supported.return_value = False
        with patch.dict(sys.modules, {'torch': torch}):
            report = self.checks._gpu_probe()
        self.assertFalse(report['bf16_supported'])
        torch.cuda.is_bf16_supported.assert_called_once_with(including_emulation=False)
        torch.randn.assert_not_called()

    def test_nf4_probe_backpropagates_and_rejects_nonfinite_gradients(self):
        torch, bnb = MagicMock(), MagicMock()
        torch.version.cuda = '12.4'
        torch.cuda.is_available.return_value = True
        torch.cuda.is_bf16_supported.return_value = True
        torch.cuda.get_device_properties.return_value = SimpleNamespace(name='GPU', total_memory=100)
        torch.cuda.get_device_capability.return_value = (8, 9)
        torch.isfinite.return_value.item.return_value = True
        torch.isfinite.return_value.all.return_value.item.return_value = False
        with patch.dict(sys.modules, {'torch': torch, 'bitsandbytes': bnb}):
            report = self.checks._gpu_probe()
        self.assertFalse(report['nf4_forward_backward'])
        bnb.nn.Linear4bit.assert_called_once_with(64, 32, bias=False,
            compute_dtype=torch.bfloat16, compress_statistics=True, quant_type='nf4')
        bnb.nn.Linear4bit.return_value.to.return_value.return_value.float.return_value.square.return_value.mean.return_value.backward.assert_called_once()

    def test_tokenizer_loader_is_local_and_enforces_imported_factory_revision(self):
        tokenizer = SimpleNamespace(eos_token_id=999)
        auto = MagicMock()
        auto.from_pretrained.return_value = tokenizer
        template_module = SimpleNamespace(__file__=str(self.root / 'lf/src/llamafactory/data/template.py'),
                                          get_template_and_fix_tokenizer=MagicMock(return_value='template'))
        modules = {'transformers': SimpleNamespace(AutoTokenizer=auto),
                   'llamafactory': SimpleNamespace(
                       __file__=str(self.root / 'lf/src/llamafactory/__init__.py'), __version__='0.9.4'),
                   'llamafactory.data': SimpleNamespace(template=template_module)}
        with patch.dict(sys.modules, modules), \
             patch.object(self.checks.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
             patch.object(self.checks, '_factory_revision', return_value=self.checks.FACTORY_REVISION):
            result = self.checks._load_tokenizer_template('/local/model', {'template': 'qwen3_nothink'})
        self.assertEqual(result, (tokenizer, 'template'))
        auto.from_pretrained.assert_called_once_with('/local/model', local_files_only=True,
                                                    trust_remote_code=False)
        args = template_module.get_template_and_fix_tokenizer.call_args.args[1]
        self.assertEqual(args.template, 'qwen3_nothink')
        auto.reset_mock()
        with patch.dict(sys.modules, modules), \
             patch.object(self.checks.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
             patch.object(self.checks, '_factory_revision', return_value='b' * 40):
            with self.assertRaises(ValueError):
                self.checks._load_tokenizer_template('/local/model', {'template': 'qwen3_nothink'})
        auto.from_pretrained.assert_not_called()

    def test_python_mismatch_fails_and_cuda_companion_versions_are_allowed(self):
        versions = dict(self.checks.CORE_VERSIONS, torchvision='0.21.0+cu124',
                        torchaudio='2.6.0+cu124')
        with self.preflight_patches(versions=versions), \
             patch.object(self.checks.platform, 'python_version', return_value='3.13.5'):
            report = self.checks.preflight(self.root, self.output)
        self.assertEqual(report['errors'], ['python_version'])

    def test_tokenizer_failure_is_reported_and_cli_returns_nonzero(self):
        run, manifest = self.fixture()
        with patch.object(self.checks, '_verify_prepared', return_value=manifest), \
             patch.object(self.checks, '_load_tokenizer_template', side_effect=ImportError('secret')), \
             contextlib.redirect_stdout(io.StringIO()):
            result = self.checks.main(['token-lengths', '--run-dir', str(run),
                                       '--output', str(self.output)])
        self.assertEqual(result, 1)
        self.assertNotIn('secret', self.output.read_text())
        self.assertEqual(json.loads(self.output.read_text())['error_type'], 'ImportError')

    def test_digest_change_between_verification_and_snapshot_blocks_dependency_loading(self):
        run, manifest = self.fixture()
        for name in ('train.yaml', 'data/train.json', 'data/validation.json'):
            with self.subTest(name=name):
                original = (run / name).read_bytes()
                def verify_and_modify(_):
                    (run / name).write_bytes(original + b' ')
                    return manifest
                self.output = self.root / (name.replace('/', '_') + '.report.json')
                with patch.object(self.checks, '_verify_prepared', side_effect=verify_and_modify), \
                     patch.object(self.checks, '_load_tokenizer_template') as loader:
                    report = self.checks.token_lengths(run, self.output)
                self.assertEqual(report['status'], 'failed')
                loader.assert_not_called()
                (run / name).write_bytes(original)

    def test_dependency_hook_mutation_cannot_change_checked_snapshot(self):
        run, manifest = self.fixture(cutoff=22)
        def mutate_and_load(model, config):
            changed = [{'instruction': '', 'input': '', 'system': '', 'output': 'Z'}]
            for split in ('train', 'validation'):
                (run / 'data' / (split + '.json')).write_text(json.dumps(changed), encoding='utf-8')
            (run / 'train.yaml').write_text('{}', encoding='utf-8')
            return SimpleNamespace(eos_token_id=999), self.fake_template()
        with patch.object(self.checks, '_verify_prepared', return_value=manifest), \
             patch.object(self.checks, '_load_tokenizer_template', side_effect=mutate_and_load):
            report = self.checks.token_lengths(run, self.output)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['splits']['train']['min'], 22)
        self.assertEqual(report['splits']['validation']['max'], 22)
        self.assertEqual(report['checked_artifacts'], manifest['artifacts'])
        self.assertIn('snapshot', report['scope'])

    def test_preflight_rejects_missing_factory_import(self):
        with self.preflight_patches(), patch.dict(sys.modules, {'llamafactory': None}):
            report = self.checks.preflight(self.root, self.output)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('factory_import', report['errors'])

    def test_preflight_rejects_factory_import_outside_requested_checkout(self):
        wrong = SimpleNamespace(__file__=str(self.root / 'other/src/llamafactory/__init__.py'),
                                __version__='0.9.4')
        with self.preflight_patches(), patch.dict(sys.modules, {'llamafactory': wrong}):
            report = self.checks.preflight(self.root, self.output)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('factory_import_path', report['errors'])

    def test_preflight_rejects_wrong_factory_version(self):
        wrong = SimpleNamespace(__file__=str(self.root / 'src/llamafactory/__init__.py'),
                                __version__='0.9.5')
        with self.preflight_patches(), patch.dict(sys.modules, {'llamafactory': wrong}):
            report = self.checks.preflight(self.root, self.output)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('factory_version', report['errors'])

    def test_preflight_rejects_tracked_changes_at_pinned_head(self):
        with self.preflight_patches(), patch.object(self.checks.subprocess, 'run',
                return_value=SimpleNamespace(returncode=1)) as git:
            report = self.checks.preflight(self.root, self.output)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('factory_tracked_changes', report['errors'])
        self.assertFalse(report['factory']['tracked_clean'])
        self.assertEqual(git.call_args.args[0],
                         ['git', '-C', str(self.root.resolve()), 'diff', '--quiet', 'HEAD', '--'])

    def test_preflight_requires_pinned_gradio(self):
        versions = dict(self.checks.CORE_VERSIONS, gradio='6.0.0')
        with self.preflight_patches(versions=versions):
            report = self.checks.preflight(self.root, self.output)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('package:gradio', report['errors'])

    def test_tokenizer_loader_rejects_dirty_source_before_loading_tokenizer(self):
        auto = MagicMock()
        modules = {'transformers': SimpleNamespace(AutoTokenizer=auto),
                   'llamafactory': SimpleNamespace(
                       __file__=str(self.root / 'src/llamafactory/__init__.py'), __version__='0.9.4'),
                   'llamafactory.data': SimpleNamespace(template=SimpleNamespace(
                       __file__=str(self.root / 'src/llamafactory/data/template.py'),
                       get_template_and_fix_tokenizer=lambda *args: 'template'))}
        with patch.dict(sys.modules, modules), \
             patch.object(self.checks, '_factory_revision', return_value=self.checks.FACTORY_REVISION), \
             patch.object(self.checks.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(ValueError):
                self.checks._load_tokenizer_template('/local/model', {'template': 'qwen3_nothink'})
        auto.from_pretrained.assert_not_called()


if __name__ == '__main__':
    unittest.main()
