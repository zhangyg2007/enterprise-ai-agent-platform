"""Prepare auditable M06 inputs using only the standard library; never train.

Local file checks establish structural readiness, not model authenticity or GPU
compatibility. The expected revision is a download instruction, not attestation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
import tempfile

from .finetune_data import json_bytes, read_bounded, sha256, validate_bundle, write_new
from .ollama import strict_json_loads

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_REVISION = 'cdbee75f17c01a7cc42f958dc650907174af0554'
COUNTS = {'smoke': {'train': 64, 'validation': 32}, 'sft': {'train': 336, 'validation': 96}}
ARTIFACTS = {'train.yaml', 'data/train.json', 'data/validation.json', 'data/dataset_info.json'}
BUNDLE_FILES = ('manifest.json', 'samples.jsonl', 'dataset_info.json', 'test-cases.jsonl',
                'manual-review-template.csv', *(f'{split}-{style}.json'
                for split in ('train', 'validation', 'test') for style in ('alpaca', 'sharegpt')))


def _object(raw: bytes, label: str) -> dict:
    value = strict_json_loads(raw.decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{label} must contain a JSON object')
    return value


def _inside(root: Path, name: str) -> Path:
    # Windows syntax must also be rejected when preparation runs on Linux.
    if (not isinstance(name, str) or not name or '\\' in name or
            PureWindowsPath(name).drive or PurePosixPath(name).is_absolute() or
            any(part in ('.', '..', '') for part in name.split('/'))):
        raise ValueError('Invalid relative artifact or shard path')
    target = root / name
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Artifact or shard path escapes its directory')
    return target


def _inspect_model(model_dir: Path) -> dict:
    model_dir = model_dir.resolve()
    if model_dir.suffix.lower() == '.gguf' or not model_dir.is_dir():
        raise ValueError('Expected a local Hugging Face model directory, not GGUF')
    if any(p.suffix.lower() == '.gguf' for p in model_dir.iterdir()):
        raise ValueError('GGUF is not a training checkpoint')
    config_raw = read_bounded(model_dir / 'config.json')
    config = _object(config_raw, 'Model config')
    if config.get('model_type') != 'qwen3':
        raise ValueError('Expected model_type qwen3')
    for name in ('tokenizer.json', 'tokenizer_config.json'):
        if not _object(read_bounded(model_dir / name), name):
            raise ValueError(f'{name} must not be empty')
    index = _object(read_bounded(model_dir / 'model.safetensors.index.json'), 'Weight index')
    weights = index.get('weight_map')
    if not isinstance(weights, dict) or not weights:
        raise ValueError('Weight index must contain a nonempty weight_map')
    for key, name in weights.items():
        if not isinstance(key, str) or not key:
            raise ValueError('Invalid tensor name in weight index')
        shard = _inside(model_dir, name)
        if shard.suffix != '.safetensors' or not shard.is_file() or shard.stat().st_size == 0:
            raise ValueError('Missing, empty, or non-safetensors model shard')
    return {'path': str(model_dir), 'config_sha256': sha256(config_raw),
            'expected_revision': EXPECTED_REVISION}


def _registry() -> dict:
    return {f'm06_{split}': {'file_name': f'{split}.json', 'formatting': 'alpaca',
            'columns': {'prompt': 'instruction', 'query': 'input', 'response': 'output', 'system': 'system'}}
            for split in ('train', 'validation')}


def _config(profile: str, model_dir: Path, run_dir: Path) -> dict:
    if profile not in COUNTS:
        raise ValueError('Profile must be smoke or sft')
    config = _object(read_bounded(ROOT / f'configs/training/m06-{profile}.yaml'), 'Training template')
    config.update(model_name_or_path=str(model_dir.resolve()), dataset_dir=str(run_dir / 'data'),
                  output_dir=str(run_dir / 'adapter'))
    return config


def _validate_prepared(run_dir: Path, manifest: dict) -> None:
    profile = manifest.get('profile')
    if profile not in COUNTS or manifest.get('counts') != COUNTS[profile]:
        raise ValueError('Prepared profile or counts mismatch')
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, dict) or set(artifacts) != ARTIFACTS:
        raise ValueError('Prepared artifact list mismatch')
    source_digest = manifest.get('source_bundle_sha256')
    if not isinstance(source_digest, str) or not re.fullmatch('[0-9a-f]{64}', source_digest):
        raise ValueError('Invalid source bundle digest')
    model = manifest.get('model')
    if (not isinstance(model, dict) or not isinstance(model.get('path'), str) or
            not Path(model['path']).is_absolute()):
        raise ValueError('Model path must be absolute')
    if model != _inspect_model(Path(model['path'])):
        raise ValueError('Model config or expected revision changed')
    raw_files = {}
    for name, digest in artifacts.items():
        raw_files[name] = read_bounded(_inside(run_dir, name))
        if sha256(raw_files[name]) != digest:
            raise ValueError(f'Prepared artifact digest mismatch: {name}')
    config = _object(raw_files['train.yaml'], 'Training config')
    if config != _config(profile, Path(model['path']), run_dir):
        raise ValueError('Prepared configuration differs from the fixed profile or absolute paths')
    if _object(raw_files['data/dataset_info.json'], 'Dataset registry') != _registry():
        raise ValueError('Prepared dataset registration mismatch')
    for split, count in COUNTS[profile].items():
        rows = strict_json_loads(raw_files[f'data/{split}.json'].decode('utf-8'))
        if not isinstance(rows, list) or len(rows) != count:
            raise ValueError('Prepared dataset count mismatch')
        if any(not isinstance(row, dict) or set(row) != {'instruction', 'input', 'output', 'system'}
               or not all(isinstance(v, str) for v in row.values()) for row in rows):
            raise ValueError('Prepared dataset columns mismatch')


def prepare_run(data_dir: Path, model_dir: Path, run_dir: Path, profile: str,
                baseline_path: Path) -> dict:
    """Validate one captured M05 bundle, then create a new isolated run directory."""
    if profile not in COUNTS:
        raise ValueError('Profile must be smoke or sft')
    data_dir, model_dir, run_dir = Path(data_dir).resolve(), Path(model_dir).resolve(), Path(run_dir).resolve()
    if run_dir.exists():
        raise FileExistsError(f'Run directory already exists: {run_dir}')
    model = _inspect_model(model_dir)
    config = _config(profile, model_dir, run_dir)
    # Copy bounded bytes once into a private snapshot. M05 validates that exact
    # snapshot; later source changes cannot affect the exported training rows.
    with tempfile.TemporaryDirectory(prefix='m06-source-') as temporary:
        snapshot = Path(temporary)
        for name in BUNDLE_FILES:
            write_new(snapshot / name, read_bounded(data_dir / name))
        validate_bundle(snapshot, Path(baseline_path))
        source_digest = sha256(read_bounded(snapshot / 'manifest.json'))
        files = {'train.yaml': json_bytes(config), 'data/dataset_info.json': json_bytes(_registry())}
        for split, count in COUNTS[profile].items():
            rows = strict_json_loads(read_bounded(snapshot / f'{split}-alpaca.json').decode('utf-8'))
            files[f'data/{split}.json'] = json_bytes(rows[:count])
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = {'status': 'preparing', 'profile': profile, 'counts': dict(COUNTS[profile]),
                'source_bundle_sha256': source_digest, 'model': model, 'artifacts': {}}
    write_new(run_dir / 'manifest.json', json_bytes(manifest))
    (run_dir / 'data').mkdir()
    for name, raw in files.items():
        write_new(run_dir / name, raw)
        manifest['artifacts'][name] = sha256(raw)
    _validate_prepared(run_dir, manifest)
    manifest['status'] = 'prepared'
    write_new(run_dir / 'manifest.tmp', json_bytes(manifest))
    os.replace(run_dir / 'manifest.tmp', run_dir / 'manifest.json')
    return manifest


def verify_prepared(run_dir: Path) -> dict:
    """Check prepared inputs; training logs and adapter outputs may coexist."""
    run_dir = Path(run_dir).resolve()
    manifest = _object(read_bounded(run_dir / 'manifest.json'), 'Run manifest')
    if manifest.get('status') != 'prepared':
        raise ValueError('Run is not prepared')
    _validate_prepared(run_dir, manifest)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='M06 local training input preparation (no training)')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--data', type=Path, required=True)
    prepare.add_argument('--model-dir', type=Path, required=True)
    prepare.add_argument('--run-dir', type=Path, required=True)
    prepare.add_argument('--profile', choices=tuple(COUNTS), required=True)
    prepare.add_argument('--baseline-cases', type=Path, default=Path('data/samples/m04-intents.jsonl'))
    verify = commands.add_parser('verify')
    verify.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = (prepare_run(args.data, args.model_dir, args.run_dir, args.profile, args.baseline_cases)
                    if args.command == 'prepare' else verify_prepared(args.run_dir))
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError, RecursionError) as error:
        print(json.dumps({'error': type(error).__name__, 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
