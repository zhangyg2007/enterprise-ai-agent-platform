"""M05: deterministic synthetic SFT data, isolated groups and auditable exports.

This validates this curriculum's generated bundle, not arbitrary customer datasets.
No model, database, tokenizer, or training framework is needed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import unicodedata

from .baseline import SYSTEM_PROMPT, load_cases, read_case_bytes, valid_answer
from .finetune_templates import CATEGORIES, TEMPLATES
from .ollama import strict_json_loads

SPLITS = ('train', 'validation', 'test')
GENERATOR_VERSION = 'm05-v1'
FIELDS = {'sample_id', 'group_id', 'split', 'category', 'template_id', 'entities', 'prompt', 'answer'}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode('utf-8')


def jsonl_bytes(rows) -> bytes:
    return ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)+'\n'
                   for row in rows).encode('utf-8')


def normalize(text: str, *, template=False) -> str:
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).casefold()
    return re.sub(r'\d+', '#', text) if template else text


def entities(number: int) -> list[str]:
    return [f'customer:{1000+number}', f'product:{2000+number}',
            f'employee:{3000+number}', f'ticket:{4000+number}']


def answer_for(category: str, number: int) -> dict:
    answer = {'intent': 'unsupported', 'customer_id': None, 'product_id': None, 'action': 'refuse'}
    if category.startswith('crm_'):
        answer.update(intent='customer_summary', action='query' if category == 'crm_query' else 'clarify')
        if category == 'crm_query':
            answer['customer_id'] = 1000+number
    elif category.startswith('scm_'):
        answer.update(intent='inventory', action='query' if category == 'scm_query' else 'clarify')
        if category == 'scm_query':
            answer['product_id'] = 2000+number
    elif category.startswith('hcm_'):
        answer.update(intent='leave_balance', action='query' if category == 'hcm_self' else 'refuse')
    elif category == 'multi':
        answer['action'] = 'clarify'
    return answer


def build_samples(seed: int) -> list[dict]:
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError('seed must be an integer in 0..4294967295')
    order = sorted(range(1, 31), key=lambda n: sha256(f'{seed}:g{n:03d}'.encode()))
    allocation = {number: ('train' if i < 21 else 'validation' if i < 27 else 'test')
                  for i, number in enumerate(order)}
    rows = []
    for number in range(1, 31):
        split, group = allocation[number], f'g{number:03d}'
        for category in CATEGORIES:
            for variant, template in enumerate(TEMPLATES[split][category]):
                rows.append({
                    'sample_id': f'{group}-{category}-{variant}', 'group_id': group,
                    'split': split, 'category': category, 'template_id': f'{split}:{category}:{variant}',
                    'entities': entities(number),
                    'prompt': template.format(customer=1000+number, product=2000+number,
                                              employee=3000+number, ticket=4000+number),
                    'answer': answer_for(category, number),
                })
    return rows


def validate_samples(samples: list[dict], baseline_cases: list[dict]) -> dict:
    if not isinstance(samples, list) or len(samples) != 480:
        raise ValueError('Expected exactly 480 generated samples')
    baseline_texts = {normalize(case['input']) for case in baseline_cases}
    baseline_entities = {f'{kind}:{case["expected"][key]}' for case in baseline_cases
                         for kind, key in (('customer', 'customer_id'), ('product', 'product_id'))
                         if case['expected'][key] is not None}
    seen_ids, seen_prompts = set(), set()
    owners = {'group': {}, 'entity': {}, 'template': {}}
    counts, categories = Counter(), {split: Counter() for split in SPLITS}
    for row in samples:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError('Invalid canonical sample fields')
        split, category, group = row['split'], row['category'], row['group_id']
        if split not in SPLITS or category not in CATEGORIES or not isinstance(group, str):
            raise ValueError('Invalid split/category/group')
        if not re.fullmatch(r'g0(?:0[1-9]|[12][0-9]|30)', group):
            raise ValueError('Invalid group number')
        number = int(group[1:])
        if row['entities'] != entities(number):
            raise ValueError('Entity lineage does not match group')
        if not isinstance(row['sample_id'], str) or row['sample_id'] in seen_ids:
            raise ValueError('Duplicate or invalid sample_id')
        if not isinstance(row['prompt'], str) or not 1 <= len(row['prompt']) <= 4000:
            raise ValueError('Invalid prompt length')
        normal, skeleton = normalize(row['prompt']), normalize(row['prompt'], template=True)
        if normal in seen_prompts or normal in baseline_texts:
            raise ValueError('Duplicate prompt or protected M04 question')
        if baseline_entities.intersection(row['entities']):
            raise ValueError('Protected M04 entity overlaps')
        for kind, values in (('group', [group]), ('entity', row['entities']), ('template', [skeleton])):
            for value in values:
                previous = owners[kind].setdefault(value, split)
                if previous != split:
                    raise ValueError(f'Cross-split {kind} leakage')
        if not valid_answer(row['answer']) or row['answer'] != answer_for(category, number):
            raise ValueError('Invalid schema or rule label')
        variants = {f'{split}:{category}:{v}': (v, template)
                    for v, template in enumerate(TEMPLATES[split][category])}
        if not isinstance(row['template_id'], str) or row['template_id'] not in variants:
            raise ValueError('Invalid template_id')
        variant, template = variants[row['template_id']]
        if (row['sample_id'] != f'{group}-{category}-{variant}' or
                row['prompt'] != template.format(customer=1000+number, product=2000+number,
                                                 employee=3000+number, ticket=4000+number)):
            raise ValueError('Sample does not match declared source template')
        seen_ids.add(row['sample_id'])
        seen_prompts.add(normal)
        counts[split] += 1
        categories[split][category] += 1
    groups = dict(Counter(owners['group'].values()))
    expected = {'train': 336, 'validation': 96, 'test': 48}
    if dict(counts) != expected or groups != {'train': 21, 'validation': 6, 'test': 3}:
        raise ValueError('Incorrect split counts')
    for split in SPLITS:
        if set(categories[split]) != set(CATEGORIES) or any(n != expected[split]//8 for n in categories[split].values()):
            raise ValueError('Incorrect per-category coverage')
    return {'counts': expected, 'groups': {s: groups[s] for s in SPLITS},
            'categories_per_split': {s: dict(categories[s]) for s in SPLITS},
            'total': len(samples), 'protected_m04_cases': len(baseline_cases),
            'synthetic_only': True, 'human_review_complete': False}


def exports(samples: list[dict]) -> dict[str, bytes]:
    files, registry = {'samples.jsonl': jsonl_bytes(samples)}, {}
    for split in SPLITS:
        rows = [row for row in samples if row['split'] == split]
        alpaca, sharegpt = [], []
        for row in rows:
            answer = json.dumps(row['answer'], ensure_ascii=False, sort_keys=True)
            alpaca.append({'instruction': row['prompt'], 'input': '', 'output': answer, 'system': SYSTEM_PROMPT})
            sharegpt.append({'system': SYSTEM_PROMPT, 'conversations': [
                {'from': 'human', 'value': row['prompt']}, {'from': 'gpt', 'value': answer}]})
        files[f'{split}-alpaca.json'] = json_bytes(alpaca)
        files[f'{split}-sharegpt.json'] = json_bytes(sharegpt)
        if split != 'test':
            registry[f'm05_{split}_alpaca'] = {
                'file_name': f'{split}-alpaca.json', 'formatting': 'alpaca',
                'columns': {'prompt': 'instruction', 'query': 'input', 'response': 'output', 'system': 'system'}}
            registry[f'm05_{split}_sharegpt'] = {
                'file_name': f'{split}-sharegpt.json', 'formatting': 'sharegpt',
                'columns': {'messages': 'conversations', 'system': 'system'},
                'tags': {'role_tag': 'from', 'content_tag': 'value', 'user_tag': 'human', 'assistant_tag': 'gpt'}}
    files['dataset_info.json'] = json_bytes(registry)
    files['test-cases.jsonl'] = jsonl_bytes([
        {'id': row['sample_id'], 'category': row['category'], 'input': row['prompt'], 'expected': row['answer']}
        for row in samples if row['split'] == 'test'])
    csv_buffer = io.StringIO(newline='')
    writer = csv.writer(csv_buffer, lineterminator='\n')
    writer.writerow(['sample_id', 'split', 'category', 'prompt', 'answer', 'reviewer', 'decision', 'note'])
    for split in SPLITS:
        for category in CATEGORIES:
            row = next(r for r in samples if r['split'] == split and r['category'] == category)
            writer.writerow([row['sample_id'], split, category, row['prompt'],
                             json.dumps(row['answer'], ensure_ascii=False, sort_keys=True), '', '', ''])
    files['manual-review-template.csv'] = csv_buffer.getvalue().encode('utf-8')
    return files


def write_new(path: Path, raw: bytes) -> None:
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def read_bounded(path: Path) -> bytes:
    if path.is_symlink():
        raise ValueError('Bundle files cannot be symbolic links')
    with path.open('rb') as stream:
        raw = stream.read(16*1024*1024+1)
    if len(raw) > 16*1024*1024:
        raise ValueError('Bundle file exceeds 16 MiB')
    return raw


def generate_bundle(output: Path, seed: int, baseline_path: Path) -> dict:
    samples = build_samples(seed)
    baseline_raw = read_case_bytes(baseline_path)
    report = validate_samples(samples, load_cases(baseline_path, raw=baseline_raw))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'status': 'building', 'generator_version': GENERATOR_VERSION, 'seed': seed,
                'baseline_sha256': sha256(baseline_raw), 'prompt_sha256': sha256(SYSTEM_PROMPT.encode()),
                'provenance': 'synthetic rule-labelled teaching data; no customer records',
                'report': report, 'artifacts': {}}
    write_new(output/'manifest.json', json_bytes(manifest))
    for name, raw in exports(samples).items():
        write_new(output/name, raw)
        manifest['artifacts'][name] = sha256(raw)
    # Verify exact written bytes before announcing completion.
    for name, expected in manifest['artifacts'].items():
        if sha256(read_bounded(output/name)) != expected:
            raise ValueError('Export verification failed')
    manifest['status'] = 'complete'
    write_new(output/'manifest.tmp', json_bytes(manifest))
    os.replace(output/'manifest.tmp', output/'manifest.json')
    return report


def validate_bundle(root: Path, baseline_path: Path) -> dict:
    root = Path(root)
    manifest = strict_json_loads(read_bounded(root/'manifest.json').decode('utf-8'))
    if not isinstance(manifest, dict) or manifest.get('status') != 'complete':
        raise ValueError('Bundle is not complete')
    if manifest.get('generator_version') != GENERATOR_VERSION:
        raise ValueError('Generator version mismatch')
    baseline_raw = read_case_bytes(baseline_path)
    if manifest.get('baseline_sha256') != sha256(baseline_raw):
        raise ValueError('Protected baseline changed')
    if manifest.get('prompt_sha256') != sha256(SYSTEM_PROMPT.encode()):
        raise ValueError('System prompt changed')
    raw = read_bounded(root/'samples.jsonl')
    samples = [strict_json_loads(line) for line in raw.decode('utf-8').splitlines()]
    report = validate_samples(samples, load_cases(baseline_path, raw=baseline_raw))
    if samples != build_samples(manifest.get('seed')) or manifest.get('report') != report:
        raise ValueError('Samples or report do not match deterministic generation')
    expected_files = exports(samples)
    if not isinstance(manifest.get('artifacts'), dict) or set(manifest['artifacts']) != set(expected_files):
        raise ValueError('Invalid artifact list')
    for name, expected_raw in expected_files.items():
        actual_raw = read_bounded(root/name)
        if actual_raw != expected_raw or manifest['artifacts'][name] != sha256(actual_raw):
            raise ValueError(f'Artifact content or digest mismatch: {name}')
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='M05 synthetic fine-tuning dataset preparation')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('generate')
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('--seed', type=int, default=42)
    check = commands.add_parser('validate')
    check.add_argument('directory', type=Path)
    for command in (create, check):
        command.add_argument('--baseline-cases', type=Path, default=Path('data/samples/m04-intents.jsonl'))
    args = parser.parse_args(argv)
    try:
        report = (generate_bundle(args.output, args.seed, args.baseline_cases) if args.command == 'generate'
                  else validate_bundle(args.directory, args.baseline_cases))
        print(json.dumps({'status': 'passed', **report}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError, RecursionError) as error:
        print(json.dumps({'error': type(error).__name__, 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
