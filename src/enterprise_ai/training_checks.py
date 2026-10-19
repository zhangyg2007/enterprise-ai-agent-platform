"""Opt-in server checks; importing this module requires only the standard library."""
from __future__ import annotations

import argparse
from importlib import import_module, metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
from types import SimpleNamespace


FACTORY_REVISION = '95ac3f2373b82662c1bd855c284d3379e6a763d3'
FACTORY_VERSION = '0.9.4'
CORE_VERSIONS = {
    'torch': '2.6.0+cu124', 'torchvision': '0.21.0', 'torchaudio': '2.6.0',
    'transformers': '4.57.1', 'peft': '0.17.1', 'bitsandbytes': '0.46.1',
    'accelerate': '1.11.0', 'datasets': '4.0.0', 'trl': '0.24.0', 'torchdata': '0.11.0',
    'gradio': '5.50.0',
}


def _factory_revision(factory_dir: Path) -> str:
    result = subprocess.run(['git', '-C', str(factory_dir.resolve()), 'rev-parse', 'HEAD'],
                            capture_output=True, text=True, check=True, timeout=20)
    revision = result.stdout.strip()
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Invalid factory revision')
    return revision


def _check_factory(factory_dir: Path) -> tuple[dict, list[str]]:
    factory_dir = factory_dir.resolve()
    expected_module = (factory_dir / 'src/llamafactory/__init__.py').resolve()
    details = {'expected_revision': FACTORY_REVISION, 'actual_revision': None,
               'expected_version': FACTORY_VERSION, 'actual_version': None,
               'expected_module_path': str(expected_module), 'actual_module_path': None,
               'tracked_clean': False}
    errors = []
    try:
        details['actual_revision'] = _factory_revision(factory_dir)
    except Exception as exc:
        details['error_type'] = type(exc).__name__
    if details['actual_revision'] != FACTORY_REVISION:
        errors.append('factory_revision')
    try:
        # Includes staged and unstaged tracked edits; generated/untracked logs are allowed.
        result = subprocess.run(['git', '-C', str(factory_dir), 'diff', '--quiet', 'HEAD', '--'],
                                capture_output=True, check=False, timeout=20)
        details['tracked_clean'] = result.returncode == 0
    except Exception as exc:
        details['tracked_error_type'] = type(exc).__name__
    if not details['tracked_clean']:
        errors.append('factory_tracked_changes')
    try:
        factory = import_module('llamafactory')
        details['actual_module_path'] = str(Path(factory.__file__).resolve())
        details['actual_version'] = getattr(factory, '__version__', None)
        if details['actual_module_path'] != str(expected_module):
            errors.append('factory_import_path')
        if details['actual_version'] != FACTORY_VERSION:
            errors.append('factory_version')
    except Exception as exc:
        details['import_error_type'] = type(exc).__name__
        errors.append('factory_import')
    return details, errors


def _gpu_probe() -> dict:
    # Deliberately tiny: no pretrained model, download, or training run.
    import torch

    result = {'cuda_available': torch.cuda.is_available(),
              'cuda_version': torch.version.cuda, 'bf16_supported': False,
              'devices': [], 'nf4_forward_backward': False}
    if not result['cuda_available']:
        return result
    index = torch.cuda.current_device()
    properties = torch.cuda.get_device_properties(index)
    result['devices'].append({'index': index, 'name': properties.name,
                              'total_memory_bytes': properties.total_memory,
                              'compute_capability': list(torch.cuda.get_device_capability(index))})
    result['bf16_supported'] = bool(torch.cuda.is_bf16_supported(including_emulation=False))
    if not result['bf16_supported']:
        return result
    try:
        import bitsandbytes as bnb

        device = torch.device('cuda', index)
        layer = bnb.nn.Linear4bit(64, 32, bias=False, compute_dtype=torch.bfloat16,
                                 compress_statistics=True, quant_type='nf4').to(device)
        inputs = torch.randn(2, 64, device=device, dtype=torch.bfloat16, requires_grad=True)
        outputs = layer(inputs)
        loss = outputs.float().square().mean()
        loss.backward()
        torch.cuda.synchronize(device)
        result['nf4_forward_backward'] = bool(
            torch.isfinite(loss).item() and inputs.grad is not None
            and torch.isfinite(inputs.grad).all().item())
    except Exception as exc:
        # Exception messages can contain local data or credentials; keep only the class.
        result['probe_error_type'] = type(exc).__name__
    return result


def _finish(report: dict, stream) -> dict:
    report['status'] = 'failed' if report['errors'] else 'passed'
    json.dump(report, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
    return report


def preflight(factory_dir: Path, output: Path) -> dict:
    """Write an exclusive JSON report, including failures; never install anything."""
    with Path(output).open('x', encoding='utf-8') as stream:
        report = {'check': 'preflight', 'scope': 'Small NF4 probe only; not 4B training acceptance.',
                  'python': platform.python_version(), 'packages': {}, 'errors': []}
        if report['python'].split('.')[:2] != ['3', '11']:
            report['errors'].append('python_version')
        for name, expected in CORE_VERSIONS.items():
            actual = None
            try:
                actual = metadata.version(name)
            except Exception:
                pass
            # PyTorch's CUDA index adds local tags to its companion wheels.
            compared = actual
            if name in ('torchvision', 'torchaudio') and actual:
                compared = actual.removesuffix('+cu124')
            report['packages'][name] = {'actual': actual, 'expected': expected}
            if compared != expected:
                report['errors'].append('package:' + name)
        report['factory'], factory_errors = _check_factory(Path(factory_dir))
        report['errors'].extend(factory_errors)
        try:
            report['gpu'] = _gpu_probe()
            for check in ('cuda_available', 'bf16_supported', 'nf4_forward_backward'):
                if not report['gpu'][check]:
                    report['errors'].append(check)
            if report['gpu']['cuda_version'] != '12.4':
                report['errors'].append('cuda_version')
        except Exception as exc:
            report['gpu'] = {'error_type': type(exc).__name__}
            report['errors'].append('gpu_probe')
        return _finish(report, stream)


def _verify_prepared(run_dir: Path) -> dict:
    from .training import verify_prepared

    return verify_prepared(run_dir)


def _load_tokenizer_template(model_dir: str, config: dict):
    import llamafactory
    from llamafactory.data import template as template_module

    # An editable checkout is required, so the API comes from the pinned source.
    factory_root = Path(llamafactory.__file__).resolve().parents[2]
    _, factory_errors = _check_factory(factory_root)
    expected_template = (factory_root / 'src/llamafactory/data/template.py').resolve()
    if factory_errors or Path(template_module.__file__).resolve() != expected_template:
        raise ValueError('Invalid LLaMA-Factory checkout')
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True,
                                              trust_remote_code=False)
    # v0.9.4 template.py get_template_and_fix_tokenizer reads these DataArguments.
    args = SimpleNamespace(template=config['template'], train_on_prompt=False,
                           tool_format=None, default_system=None, enable_thinking=None)
    template = template_module.get_template_and_fix_tokenizer(tokenizer, args)
    return tokenizer, template


def token_lengths(run_dir: Path, output: Path) -> dict:
    """Measure untruncated train/validation conversations using the LF template."""
    with Path(output).open('x', encoding='utf-8') as stream:
        report = {'check': 'token-lengths', 'scope': 'Lengths of the checked in-memory snapshot only; '
                  'LF training loader and GPU training unverified.',
                  'index_base': 0, 'splits': {}, 'checked_artifacts': {}, 'errors': []}
        try:
            from .finetune_data import read_bounded, sha256

            run_dir = Path(run_dir)
            manifest = _verify_prepared(run_dir)
            # Each byte sequence used below is read once and verified against the original
            # manifest before heavy imports. Later filesystem edits cannot enter the calculation.
            snapshot = {}
            for name in ('train.yaml', 'data/train.json', 'data/validation.json'):
                raw = read_bounded(run_dir / name)
                digest = sha256(raw)
                if digest != manifest['artifacts'][name]:
                    raise ValueError('Prepared snapshot digest mismatch')
                snapshot[name] = raw
                report['checked_artifacts'][name] = digest
            config = json.loads(snapshot['train.yaml'].decode('utf-8'))
            cutoff = config['cutoff_len']
            if type(cutoff) is not int or cutoff <= 0 or config['template'] != 'qwen3_nothink':
                raise ValueError('Invalid training template or cutoff')
            report.update(cutoff_len=cutoff, template=config['template'])
            tokenizer, template = _load_tokenizer_template(manifest['model']['path'], config)
            report['tokenizer'] = {'class': type(tokenizer).__name__,
                                   'local_files_only': True, 'trust_remote_code': False,
                                   'eos_token_id': tokenizer.eos_token_id}
            if tokenizer.eos_token_id is None:
                raise ValueError('Missing EOS token')
            for split in ('train', 'validation'):
                rows = json.loads(snapshot['data/' + split + '.json'].decode('utf-8'))
                if not rows or len(rows) != manifest['counts'][split]:
                    raise ValueError('Dataset count mismatch')
                lengths, over, empty = [], [], []
                for index, row in enumerate(rows):
                    if not all(isinstance(row.get(key), str) for key in
                               ('instruction', 'input', 'output', 'system')):
                        raise ValueError('Invalid training row')
                    user = '\n'.join(part for part in (row['instruction'], row['input']) if part)
                    messages = [{'role': 'user', 'content': user},
                                {'role': 'assistant', 'content': row['output']}]
                    prompt_ids, answer_ids = template.encode_oneturn(
                        tokenizer, messages, system=row['system'], tools=None)
                    if not row['output'].strip() or not answer_ids:
                        empty.append(index)
                    # LF supervised.py appends one EOS only for efficient_eos templates.
                    # qwen3_nothink already contains its end marker and trailing newline.
                    length = len(prompt_ids) + len(answer_ids) + int(template.efficient_eos)
                    lengths.append(length)
                    if length > cutoff:
                        over.append(index)
                report['splits'][split] = {'count': len(rows), 'min': min(lengths),
                    'max': max(lengths), 'over_cutoff_indices': over, 'empty_answer_indices': empty}
                if over:
                    report['errors'].append(split + ':over_cutoff')
                if empty:
                    report['errors'].append(split + ':empty_answer')
        except Exception as exc:
            report['errors'].append('token_length_check')
            report['error_type'] = type(exc).__name__
        return _finish(report, stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    pre = commands.add_parser('preflight')
    pre.add_argument('--factory-dir', required=True, type=Path)
    pre.add_argument('--output', required=True, type=Path)
    lengths = commands.add_parser('token-lengths')
    lengths.add_argument('--run-dir', required=True, type=Path)
    lengths.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = (preflight(args.factory_dir, args.output) if args.command == 'preflight'
                  else token_lengths(args.run_dir, args.output))
    except Exception as exc:
        print('Check could not write a report: ' + type(exc).__name__, file=sys.stderr)
        return 1
    print(args.command + ': ' + report['status'])
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
