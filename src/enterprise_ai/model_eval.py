"""M07: preserve all selected cases and compare only matched, complete experiments."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
import math
from pathlib import Path
import platform
import sys
import time

from .baseline import FIELDS, SCHEMA, SYSTEM_PROMPT, empty_score, load_cases, score_response
from .evaluation import revision, summarize, utc_now, write_json
from .finetune_data import json_bytes, read_bounded, sha256, write_new
from .model_delivery import (COUNTS, GENERATION, configurations, fingerprint_model,
                             read_artifact, verify_release, verify_export, validate_export_state)
from .ollama import strict_json_loads
from .training import _object
from .training_checks import CORE_VERSIONS, _check_factory

VARIANTS = ('base', 'adapter', 'adapter-bf16', 'merged')


class FactoryBackend:
    """Only constructed on the training server; imports no GPU libraries at module load."""
    def __init__(self, config):
        import llamafactory
        factory_root = Path(llamafactory.__file__).resolve().parents[2]
        factory, errors = _check_factory(factory_root)
        if errors or sys.version_info[:2] != (3, 11):
            raise ValueError('M06 Python/Factory environment mismatch')
        packages = {name: metadata.version(name) for name in CORE_VERSIONS}
        for name, expected in CORE_VERSIONS.items():
            actual = packages[name]
            if (actual != expected if '+' in expected else actual.split('+')[0] != expected):
                raise ValueError('M06 package mismatch: ' + name)
        from transformers import set_seed
        from llamafactory.chat import ChatModel
        set_seed(42)
        self.model = ChatModel(config)
        engine = self.model.engine
        stops = engine.template.get_stop_token_ids(engine.tokenizer)
        if set(stops) != {engine.tokenizer.eos_token_id}:
            raise ValueError('Template stop tokens differ from HF finish_reason EOS semantics')
        self.runtime = {'backend': 'llamafactory', 'python': platform.python_version(),
                        'packages': packages, 'factory': factory}

    def chat(self, user_text, system_prompt):
        engine = self.model.engine
        messages = [{'role': 'user', 'content': user_text}]
        ids, _ = engine.template.encode_oneturn(
            engine.tokenizer, messages + [{'role': 'assistant', 'content': ''}], system_prompt, None)
        if len(ids) + GENERATION['max_new_tokens'] > 2048:
            raise ValueError('Prompt exceeds context budget; no silent truncation')
        values = self.model.chat(messages, system=system_prompt, **GENERATION)
        if len(values) != 1:
            raise ValueError('Expected exactly one generation')
        response = values[0]
        if response.prompt_length != len(ids):
            raise ValueError('Encoded prompt length changed')
        return {'text': response.response_text, 'finish_reason': response.finish_reason,
                'prompt_tokens': response.prompt_length, 'output_tokens': response.response_length}


def validate_reply(reply):
    if (not isinstance(reply, dict)
            or set(reply) != {'text', 'finish_reason', 'prompt_tokens', 'output_tokens'}
            or not isinstance(reply['text'], str) or len(reply['text'].encode()) > 1024 * 1024
            or reply['finish_reason'] not in ('stop', 'length')
            or any(type(reply[k]) is not int or reply[k] < 0 for k in ('prompt_tokens', 'output_tokens'))
            or reply['prompt_tokens'] + 256 > 2048 or reply['output_tokens'] > 256):
        raise ValueError('Invalid generation response')


def reply_score(reply, expected):
    return score_response({'message': {'content': reply['text']},
                           'done_reason': reply['finish_reason']}, expected)


def metrics(records, cases, *, stable, subset):
    summary = summarize(records, identity_stable=stable, is_subset=subset)
    categories = {}
    refused, false_accepts = 0, 0
    for case, record in zip(cases, records):
        group = categories.setdefault(case['category'], {'total': 0, 'correct': 0})
        group['total'] += 1
        group['correct'] += int(record['score']['exact_match'])
        if case['expected']['action'] == 'refuse':
            refused += 1
            if record['score']['schema_valid'] and record['raw'] is not None:
                answer = strict_json_loads(record['raw']['text'])
                false_accepts += int(answer['action'] == 'query')
    summary.update(categories=categories, refusal_cases=refused, refusal_false_accepts=false_accepts)
    return summary


def code_fingerprint():
    root = Path(__file__).parent
    return {name: sha256((root / name).read_bytes()) for name in
            ('model_eval.py', 'model_delivery.py', 'baseline.py', 'ollama.py', 'training_checks.py')}


def evaluate_release(release_dir, variant, split, output_dir, limit=None, backend_factory=None):
    release, output = Path(release_dir).resolve(), Path(output_dir)
    if output.exists():
        raise FileExistsError(output)
    if variant not in VARIANTS or split not in COUNTS:
        raise ValueError('Unknown variant or split')
    frozen = verify_release(release)
    case_raw = read_artifact(release, frozen, f'cases-{split}.jsonl')
    cases = load_cases(release / f'cases-{split}.jsonl', raw=case_raw)
    if limit is not None and (type(limit) is not int or not 1 <= limit <= len(cases)):
        raise ValueError('Invalid evaluation limit')
    selected = cases if limit is None else cases[:limit]
    config = _object(read_artifact(release, frozen, variant + '.yaml'), 'config')
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version': 'm07-evaluation-v1', 'status': 'running', 'started_at': utc_now(),
                'variant': variant, 'split': split, 'source_count': len(cases),
                'selected_count': len(selected), 'release': frozen,
                'release_sha256': sha256(json_bytes(frozen)), 'release_dir': str(release),
                'source_cases_sha256': sha256(case_raw), 'config': config,
                'prompt_sha256': sha256(SYSTEM_PROMPT.encode()), 'schema_sha256': sha256(json_bytes(SCHEMA)),
                'generation': dict(GENERATION), 'seed': 42, 'code': revision(),
                'code_sha256': code_fingerprint(), 'runtime': None, 'identity_stable': False,
                'merged_files': None, 'export_state': None, 'artifacts': {}}
    write_json(output / 'manifest.json', manifest)
    write_new(output / 'cases.json', json_bytes(selected))
    write_new(output / 'source-cases.jsonl', case_raw)
    records, backend, error = [], None, None
    try:
        if variant == 'merged':
            manifest['export_state'] = verify_export(release, frozen)
            manifest['merged_files'] = manifest['export_state']['files']
        backend = (backend_factory or FactoryBackend)(config)
        # Snapshot metadata instead of retaining a mutable backend reference.
        manifest['runtime'] = strict_json_loads(json_bytes(backend.runtime).decode())
        if not isinstance(manifest['runtime'], dict) or not manifest['runtime']:
            raise ValueError('Backend runtime metadata missing')
        if verify_release(release) != frozen:
            raise ValueError('Release changed during initialization')
        if variant == 'merged' and verify_export(release, frozen) != manifest['export_state']:
            raise ValueError('Export evidence changed during initialization')
    except Exception as exc:
        error = 'initialization_' + type(exc).__name__
    with (output / 'records.jsonl').open('xb') as stream:
        for case in selected:
            started = time.perf_counter()
            raw = None
            record_error = 'not_executed' if error else None
            if error and not records:
                record_error = error
            if not error:
                try:
                    raw = backend.chat(case['input'], SYSTEM_PROMPT)
                    validate_reply(raw)
                except Exception as exc:
                    raw = None
                    error = record_error = 'generation_' + type(exc).__name__
            score = empty_score(record_error) if record_error else reply_score(raw, case['expected'])
            record = {'id': case['id'], 'raw': raw, 'error': record_error, 'score': score,
                      'wall_seconds': time.perf_counter() - started}
            records.append(record)
            stream.write((json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode())
            stream.flush()
    try:
        stable = verify_release(release) == frozen
        if variant == 'merged':
            stable = stable and manifest['export_state'] == verify_export(release, frozen)
    except (ValueError, OSError, KeyError, TypeError):
        stable = False
    summary = metrics(records, selected, stable=stable, subset=len(selected) != len(cases))
    write_new(output / 'summary.json', json_bytes(summary))
    manifest.update(status='completed' if not error and stable else 'failed',
                    finished_at=utc_now(), identity_stable=stable)
    manifest['artifacts'] = {name: sha256(read_bounded(output / name))
                             for name in ('cases.json', 'source-cases.jsonl', 'records.jsonl', 'summary.json')}
    write_json(output / 'manifest.json', manifest)
    return summary


def _read_run(directory):
    directory = Path(directory)
    m = _object(read_bounded(directory / 'manifest.json'), 'evaluation')
    if (m.get('schema_version') != 'm07-evaluation-v1' or m.get('status') != 'completed'
            or m.get('identity_stable') is not True or m.get('split') not in COUNTS
            or m.get('variant') not in VARIANTS or m.get('selected_count') != COUNTS[m['split']]
            or m.get('source_count') != COUNTS[m['split']]
            or m.get('generation') != GENERATION or m.get('seed') != 42
            or m.get('prompt_sha256') != sha256(SYSTEM_PROMPT.encode())
            or m.get('schema_sha256') != sha256(json_bytes(SCHEMA))):
        raise ValueError('Incomplete, subset, changed contract, or failed run')
    if set(m.get('artifacts', {})) != {'cases.json', 'source-cases.jsonl', 'records.jsonl', 'summary.json'}:
        raise ValueError('Incomplete evaluation artifacts')
    raw = {}
    for name, digest in m['artifacts'].items():
        raw[name] = read_bounded(directory / name)
        if sha256(raw[name]) != digest:
            raise ValueError('Evaluation artifact changed: ' + name)
    frozen = m['release']
    if sha256(json_bytes(frozen)) != m['release_sha256']:
        raise ValueError('Release identity changed')
    expected_config = configurations(Path(frozen['assets']['base']['path']),
                                     Path(frozen['assets']['adapter']['path']), Path(m['release_dir']))
    if m['config'] != expected_config[m['variant'] + '.yaml']:
        raise ValueError('Variant config mismatch')
    if m['source_cases_sha256'] != frozen['artifacts'][f"cases-{m['split']}.jsonl"]:
        raise ValueError('Source case identity mismatch')
    cases = strict_json_loads(raw['cases.json'].decode())
    if not isinstance(cases, list):
        raise ValueError('Cases must be a list')
    source_cases = load_cases(directory / 'source-cases.jsonl', raw=raw['source-cases.jsonl'])
    if sha256(raw['source-cases.jsonl']) != m['source_cases_sha256'] or cases != source_cases:
        raise ValueError('Selected cases differ from the frozen source')
    records = [strict_json_loads(line) for line in raw['records.jsonl'].decode().splitlines()]
    if len(cases) != m['selected_count'] or len(records) != len(cases):
        raise ValueError('Missing cases or records')
    for case, record in zip(cases, records):
        if (record['id'] != case['id'] or record['error'] is not None
                or type(record['wall_seconds']) not in (int, float)
                or not math.isfinite(record['wall_seconds']) or record['wall_seconds'] < 0):
            raise ValueError('Invalid record identity, error, or duration')
        validate_reply(record['raw'])
        if record['score'] != reply_score(record['raw'], case['expected']):
            raise ValueError('Recorded score differs from recomputed score')
    summary = metrics(records, cases, stable=True, subset=False)
    if summary != strict_json_loads(raw['summary.json'].decode()):
        raise ValueError('Summary differs from recomputed metrics')
    if not isinstance(m['runtime'], dict) or not m['runtime']:
        raise ValueError('Missing runtime identity')
    if m['variant'] == 'merged':
        validate_export_state(m.get('export_state'), frozen)
        if m['merged_files'] != m['export_state']['files']:
            raise ValueError('Merged identity differs from successful export evidence')
    return m, cases, records, summary


def compare_runs(left_dir, right_dir, output_file):
    output = Path(output_file)
    if output.exists():
        raise FileExistsError(output)
    left, cases, left_rows, before = _read_run(left_dir)
    right, right_cases, right_rows, after = _read_run(right_dir)
    pair = (left['variant'], right['variant'])
    if pair not in (('base', 'adapter'), ('adapter-bf16', 'merged')):
        raise ValueError('Only matched 4bit fine-tuning or BF16 export pairs are comparable')
    common = ('split', 'release_sha256', 'source_cases_sha256', 'prompt_sha256', 'schema_sha256',
              'generation', 'seed', 'runtime', 'code_sha256')
    if cases != right_cases or any(left[k] != right[k] for k in common):
        raise ValueError('Cases, release, contract, code, or runtime mismatch')
    groups = {'improved': [], 'regressed': [], 'unchanged': []}
    for case, a, b in zip(cases, left_rows, right_rows):
        old, new = a['score']['exact_match'], b['score']['exact_match']
        groups['unchanged' if old == new else 'improved' if new else 'regressed'].append(case['id'])
    report = {'comparison': 'finetuning_4bit' if pair[0] == 'base' else 'export_regression',
              'before': before, 'after': after, **groups,
              'exact_match_delta_pp': 100 * (after['exact_match_rate'] - before['exact_match_rate']),
              'field_delta_pp': {f: 100 * (after['field_accuracy'][f] - before['field_accuracy'][f]) for f in FIELDS},
              'text_equal_rate': sum(a['raw']['text'] == b['raw']['text']
                                     for a, b in zip(left_rows, right_rows)) / len(cases),
              'release_sha256': left['release_sha256'], 'merged_files': right['merged_files'],
              'evidence': {side: {'manifest_sha256': sha256(read_bounded(Path(path) / 'manifest.json')),
                                  'path': str(Path(path).resolve())}
                           for side, path in (('before', left_dir), ('after', right_dir))}}
    output.parent.mkdir(parents=True, exist_ok=True)
    write_new(output, json_bytes(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='M07 matched model evaluation and comparison')
    commands = parser.add_subparsers(dest='command', required=True)
    evaluate = commands.add_parser('evaluate')
    evaluate.add_argument('--release', type=Path, required=True)
    evaluate.add_argument('--variant', choices=VARIANTS, required=True)
    evaluate.add_argument('--split', choices=tuple(COUNTS), required=True)
    evaluate.add_argument('--limit', type=int)
    evaluate.add_argument('--output', type=Path, required=True)
    compare = commands.add_parser('compare')
    for name in ('before', 'after', 'output'):
        compare.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        value = (evaluate_release(args.release, args.variant, args.split, args.output, args.limit)
                 if args.command == 'evaluate' else compare_runs(args.before, args.after, args.output))
        print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        return 2 if args.command == 'evaluate' and not value['comparison_eligible'] else 0
    except (ValueError, OSError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
