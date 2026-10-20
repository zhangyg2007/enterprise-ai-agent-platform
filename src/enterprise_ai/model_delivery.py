"""M07: freeze auditable evaluation inputs; never load or merge model weights."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from .baseline import load_cases
from .finetune_data import json_bytes, read_bounded, sha256, validate_bundle, write_new
from .ollama import strict_json_loads
from .training import BUNDLE_FILES, _inside, _object, verify_prepared

GENERATION = {'do_sample': False, 'max_new_tokens': 256,
              'num_return_sequences': 1, 'repetition_penalty': 1.0}
COUNTS = {'validation': 96, 'test': 48}
CONFIG_NAMES = ('base', 'adapter', 'adapter-bf16', 'merged', 'export')
ARTIFACTS = {*(name + '.yaml' for name in CONFIG_NAMES),
             'cases-validation.jsonl', 'cases-test.jsonl'}


def _fingerprints(root: Path, names) -> dict:
    result = {}
    for name in sorted(set(names)):
        path = _inside(root, name)
        digest, size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
                size += len(block)
        if not size:
            raise ValueError(f'Empty model asset: {name}')
        result[name] = {'size': size, 'sha256': digest.hexdigest()}
    return result


def fingerprint_model(path: Path) -> dict:
    path = Path(path).resolve()
    config = _object(read_bounded(path / 'config.json'), 'model config')
    if config.get('model_type') != 'qwen3':
        raise ValueError('Expected qwen3 model')
    names = ['config.json', 'tokenizer.json', 'tokenizer_config.json']
    for name in names[1:]:
        if not _object(read_bounded(path / name), name):
            raise ValueError('Empty tokenizer configuration')
    index_path = path / 'model.safetensors.index.json'
    if index_path.exists():
        weights = _object(read_bounded(index_path), 'weight index').get('weight_map')
        if not isinstance(weights, dict) or not weights:
            raise ValueError('Missing weight_map')
        if any(not isinstance(n, str) or not n.endswith('.safetensors') for n in weights.values()):
            raise ValueError('Invalid model shards')
        names.extend(['model.safetensors.index.json', *weights.values()])
    else:
        names.append('model.safetensors')
    for name in ('generation_config.json', 'vocab.json', 'merges.txt', 'special_tokens_map.json',
                 'added_tokens.json', 'chat_template.jinja'):
        if (path / name).exists():
            names.append(name)
    return _fingerprints(path, names)


def fingerprint_adapter(path: Path) -> dict:
    path = Path(path).resolve()
    config = _object(read_bounded(path / 'adapter_config.json'), 'adapter config')
    if config.get('peft_type') != 'LORA':
        raise ValueError('Expected LORA adapter')
    return _fingerprints(path, ['adapter_config.json', 'adapter_model.safetensors'])


def configurations(model: Path, adapter: Path, release: Path) -> dict:
    common = {'model_name_or_path': str(model), 'template': 'qwen3_nothink',
              'stage': 'sft', 'finetuning_type': 'lora', 'trust_remote_code': False,
              'infer_backend': 'huggingface', 'infer_dtype': 'bfloat16',
              'flash_attn': 'sdpa', 'cutoff_len': 2048, **GENERATION}
    base = {**common, 'quantization_bit': 4, 'quantization_method': 'bitsandbytes',
            'quantization_type': 'nf4', 'double_quantization': True}
    return {
        'base.yaml': base,
        'adapter.yaml': {**base, 'adapter_name_or_path': str(adapter)},
        'adapter-bf16.yaml': {**common, 'adapter_name_or_path': str(adapter)},
        'merged.yaml': {**common, 'model_name_or_path': str(release / 'merged-model')},
        'export.yaml': {'model_name_or_path': str(model), 'adapter_name_or_path': str(adapter),
                        'template': 'qwen3_nothink', 'finetuning_type': 'lora', 'stage': 'sft',
                        'trust_remote_code': False, 'export_dir': str(release / 'merged-model'),
                        'export_device': 'cpu', 'infer_dtype': 'bfloat16',
                        'export_size': 2, 'export_legacy_format': False},
    }


def read_artifact(release_dir: Path, manifest: dict, name: str) -> bytes:
    if name not in ARTIFACTS:
        raise ValueError('Unknown release artifact')
    raw = read_bounded(_inside(Path(release_dir), name))
    if sha256(raw) != manifest['artifacts'].get(name):
        raise ValueError(f'Release artifact changed: {name}')
    return raw


def verify_release(release_dir: Path, *, check_assets=True) -> dict:
    release = Path(release_dir).resolve()
    manifest = _object(read_bounded(release / 'manifest.json'), 'release')
    if (manifest.get('schema_version') != 'm07-v1' or manifest.get('status') != 'prepared'
            or manifest.get('counts') != COUNTS or manifest.get('generation') != GENERATION
            or manifest.get('seed') != 42 or set(manifest.get('artifacts', {})) != ARTIFACTS):
        raise ValueError('Invalid or incomplete release')
    assets = manifest['assets']
    model, adapter = (Path(assets[k]['path']) for k in ('base', 'adapter'))
    if not model.is_absolute() or not adapter.is_absolute():
        raise ValueError('Asset paths must be absolute')
    if manifest.get('merged_path') != str(release / 'merged-model'):
        raise ValueError('Merged model path changed')
    for name, expected in configurations(model, adapter, release).items():
        if _object(read_artifact(release, manifest, name), name) != expected:
            raise ValueError(f'Release configuration changed: {name}')
    for split, count in COUNTS.items():
        name = f'cases-{split}.jsonl'
        if len(load_cases(release / name, raw=read_artifact(release, manifest, name))) != count:
            raise ValueError('Case count mismatch')
    if check_assets:
        if (assets['base']['files'] != fingerprint_model(model)
                or assets['adapter']['files'] != fingerprint_adapter(adapter)):
            raise ValueError('Model or adapter assets changed')
        config = _object(read_bounded(adapter / 'adapter_config.json'), 'adapter')
        if config.get('base_model_name_or_path') != str(model):
            raise ValueError('Adapter base model mismatch')
    return manifest


def prepare_release(training_run: Path, data_dir: Path, adapter_dir: Path,
                    output_dir: Path, baseline_path: Path) -> dict:
    run, data, adapter, output = [Path(p).resolve() for p in
                                 (training_run, data_dir, adapter_dir, output_dir)]
    if output.exists():
        raise FileExistsError(output)
    run_raw = read_bounded(run / 'manifest.json')
    prepared = verify_prepared(run)
    if prepared != _object(run_raw, 'training run'):
        raise ValueError('Training manifest changed during validation')
    if prepared['profile'] != 'sft':
        raise ValueError('M07 requires sft, not smoke')
    if not adapter.is_relative_to((run / 'adapter').resolve()):
        raise ValueError('Adapter must belong to the training run')
    model = Path(prepared['model']['path'])
    config = _object(read_bounded(adapter / 'adapter_config.json'), 'adapter')
    if config.get('base_model_name_or_path') != str(model):
        raise ValueError('Adapter base model mismatch')
    assets = {'base': {'path': str(model), 'files': fingerprint_model(model)},
              'adapter': {'path': str(adapter), 'files': fingerprint_adapter(adapter)}}
    with tempfile.TemporaryDirectory(prefix='m07-source-') as temporary:
        snapshot = Path(temporary)
        for name in BUNDLE_FILES:
            write_new(snapshot / name, read_bounded(data / name))
        validate_bundle(snapshot, Path(baseline_path))
        bundle_digest = sha256(read_bounded(snapshot / 'manifest.json'))
        if bundle_digest != prepared['source_bundle_sha256']:
            raise ValueError('M05 source bundle does not match the sft run')
        samples = [strict_json_loads(line) for line in
                   read_bounded(snapshot / 'samples.jsonl').decode().splitlines()]
        files = {name: json_bytes(value) for name, value in configurations(model, adapter, output).items()}
        for split in COUNTS:
            cases = [{'id': s['sample_id'], 'category': s['category'],
                      'input': s['prompt'], 'expected': s['answer']}
                     for s in samples if s['split'] == split]
            files[f'cases-{split}.jsonl'] = b''.join(
                (json.dumps(case, ensure_ascii=False, sort_keys=True) + '\n').encode() for case in cases)
        # Preserve the canonical test export from the same validated snapshot.
        files['cases-test.jsonl'] = read_bounded(snapshot / 'test-cases.jsonl')
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version': 'm07-v1', 'status': 'preparing',
                'source_training_run_manifest_sha256': sha256(run_raw),
                'source_bundle_sha256': bundle_digest, 'assets': assets,
                'merged_path': str(output / 'merged-model'), 'generation': dict(GENERATION),
                'seed': 42, 'counts': dict(COUNTS), 'artifacts': {}}
    write_new(output / 'manifest.json', json_bytes(manifest))
    for name, raw in files.items():
        write_new(output / name, raw)
        manifest['artifacts'][name] = sha256(raw)
    if assets['base']['files'] != fingerprint_model(model) or assets['adapter']['files'] != fingerprint_adapter(adapter):
        raise ValueError('Assets changed during preparation')
    manifest['status'] = 'prepared'
    write_new(output / 'manifest.tmp', json_bytes(manifest))
    os.replace(output / 'manifest.tmp', output / 'manifest.json')
    return verify_release(output)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='M07 model delivery preparation (no model loading)')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    for name in ('training-run', 'data', 'adapter-dir', 'output'):
        prepare.add_argument('--' + name, type=Path, required=True)
    prepare.add_argument('--baseline-cases', type=Path, default=Path('data/samples/m04-intents.jsonl'))
    verify = commands.add_parser('verify')
    verify.add_argument('--release', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        value = (prepare_release(args.training_run, args.data, args.adapter_dir, args.output, args.baseline_cases)
                 if args.command == 'prepare' else verify_release(args.release))
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError, RecursionError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
