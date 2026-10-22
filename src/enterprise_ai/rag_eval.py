"""Document-level retrieval evaluation and a reproducible offline freshness lab."""
import argparse
import copy
import hashlib
import json
import platform
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from .document_pipeline import _digest, _integer, _json, ingest
from .document_samples import generate_corpus
from .ollama import strict_json_loads
from .rag_index import assert_fresh, build_index, load_index, write_new
from .rag_models import FixtureEncoder, LocalReranker, encoder_options, make_encoder
from .rag_search import evidence_answer, options, search


def _key(document):
    return (document['tenant_id'], document['document_id'], document['version'])


def validate_cases(cases, tenant=None):
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
        raise ValueError('expected 1..1000 evaluation cases')
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {'id', 'tenant_id', 'query', 'roles', 'as_of', 'expected_documents'}:
            raise ValueError('invalid case fields')
        if (not isinstance(case['id'], str) or not case['id'] or case['id'] in ids
                or not _integer(case['tenant_id']) or (tenant is not None and case['tenant_id'] != tenant)):
            raise ValueError('invalid case id/tenant')
        ids.add(case['id'])
        options(case['query'], case['roles'], case['as_of'], 'hybrid', 5, 20)
        labels = case['expected_documents']
        if not isinstance(labels, list): raise ValueError('expected document labels must be a list')
        seen = set()
        for label in labels:
            if (not isinstance(label, dict) or set(label) != {'tenant_id', 'document_id', 'version'}
                    or label['tenant_id'] != case['tenant_id'] or not _integer(label['version'])
                    or not isinstance(label['document_id'], str)
                    or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', label['document_id'])):
                raise ValueError('invalid relevance label')
            if _key(label) in seen: raise ValueError('duplicate relevance label')
            seen.add(_key(label))
    return cases


def load_cases(path, tenant=None):
    raw = Path(path).read_bytes()
    if len(raw) > 4*1024*1024: raise ValueError('case file too large')
    dataset = strict_json_loads(raw.decode('utf-8'))
    if dataset.get('schema_version') != 'm09-cases-v1': raise ValueError('unknown case schema')
    cases = validate_cases(dataset['cases'])
    return validate_cases([c for c in cases if tenant is None or c['tenant_id'] == tenant], tenant)


def score_rows(rows):
    """Chunk top-k is collapsed to unique (tenant, document, version) ranks."""
    answerable = rejected = errors = clean = candidates = 0
    recall = reciprocal = 0.0
    for row in rows:
        expected = {_key(d) for d in row['expected_documents']}
        ranked = list(dict.fromkeys(_key(d) for d in row['documents'])) if row['status'] == 'ok' else []
        errors += row['status'] != 'ok'
        if expected:
            answerable += 1
            recall += len(expected.intersection(ranked))/len(expected)
            reciprocal += next((1/r for r, key in enumerate(ranked, 1) if key in expected), 0)
        else:
            rejected += 1
            candidates += bool(ranked)
            clean += row['status'] == 'ok' and not ranked
    return {'questions': len(rows), 'answerable_questions': answerable, 'rejection_questions': rejected,
            'errors': errors, 'recall_at_k': recall/answerable if answerable else None,
            'mrr_at_k': reciprocal/answerable if answerable else None,
            'rejected_candidate_rate': candidates/rejected if rejected else None,
            'clean_rejection_rate': clean/rejected if rejected else None}


def _code_identity():
    root = Path(__file__).resolve().parents[2]
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError): head = None
    # HEAD alone misses uncommitted fixes; hash all package sources too.
    sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(Path(__file__).parent.glob('*.py'))}
    return {'git_head': head, 'source_digest': _digest(sources), 'python': platform.python_version()}


def evaluate(index, state, cases, encoder, output, mode='hybrid', *, k=5, candidates=20, reranker=None):
    validate_cases(cases, index['tenant_id'])
    options(cases[0]['query'], cases[0]['roles'], cases[0]['as_of'], mode, k, candidates)
    assert_fresh(index, state)
    if Path(output).exists(): raise FileExistsError(output)
    if encoder.identity != index['model']: raise ValueError('evaluation model mismatch')
    rows = []
    for case in cases:
        row = {**copy.deepcopy(case), 'status': 'error', 'documents': [], 'chunk_ids': [], 'citations': []}
        try:
            result = search(index, state, case['query'], roles=case['roles'], as_of=case['as_of'],
                            mode=mode, k=k, candidates=candidates, encoder=encoder, reranker=reranker)
            answer = evidence_answer(result)
            row.update(status='ok', documents=[{key: hit['chunk'][key] for key in
                       ('tenant_id', 'document_id', 'version')} for hit in result['hits']],
                       chunk_ids=[h['chunk']['chunk_id'] for h in result['hits']],
                       citations=answer['citations'], answer_status=answer['status'], hits=result['hits'])
        except Exception as exc:
            # Retain failed questions in denominators without exposing arbitrary exception content.
            row['error_type'] = type(exc).__name__
        rows.append(row)
    encoder.verify()
    if reranker is not None: reranker.verify()
    assert_fresh(index, state)
    report = {'schema_version': 'm09-evaluation-v1',
              'kind': 'fixture-pipeline-check-not-semantic-quality' if index['model']['kind'] == 'synthetic-fixture'
                      else 'local-model-retrieval-experiment',
              'tenant_id': index['tenant_id'], 'index_digest': index['digest'],
              'source_digest': index['source_digest'], 'cases_digest': _digest(cases),
              'model': index['model'], 'reranker': reranker.identity if reranker else None,
              'code': _code_identity(), 'mode': mode, 'k': k, 'candidates': candidates,
              'metric_scope': 'document ranks within chunk top-k; public development set; not held-out quality',
              'rejection_note': 'candidate rate includes unrelated hits; errors are not clean rejections',
              'metrics': score_rows(rows), 'rows': rows}
    write_new(output, report)
    return report


def run_demo(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = output/'sources'
    catalog = generate_corpus(source, formats='md')
    state = output/'state.sqlite'
    ingest(source/'catalog.json', source, state, output/'01-ingest.json')
    encoder = FixtureEncoder()
    old = build_index(state, 1, 1, encoder, output/'02-index-v1.json')
    before = {}
    for mode in ('keyword', 'dense', 'hybrid'):
        result = search(old, state, '折扣超过10%报价审批', roles=['sales'], as_of='2026-10-21',
                        mode=mode, encoder=encoder)
        before[mode] = {'retrieval': result, 'answer': evidence_answer(result)}
    write_new(output/'03-retrieval.json', before)
    changed = copy.deepcopy(catalog['documents'][0])
    path = source/changed['source_path']
    path.write_bytes(path.read_bytes()+'\n更新：报价审批仅employee角色可见。\n'.encode())
    changed.update(version=2, roles=['employee'], sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    removed = catalog['documents'][1]
    changes = {'schema_version': 'm08-catalog-v1', 'documents': [changed,
               {'tenant_id': 1, 'document_id': removed['document_id'], 'version': 2, 'op': 'delete'}]}
    write_new(output/'04-changes.json', changes)
    ingest(output/'04-changes.json', source, state, output/'05-update-report.json')
    stale = False
    try: search(old, state, '报价审批', roles=['sales'], as_of='2026-10-21', encoder=encoder)
    except ValueError as exc:
        if 'stale index' not in str(exc): raise
        stale = True
    new = build_index(state, 1, 2, encoder, output/'06-index-v2.json')
    sales = search(new, state, '报价审批', roles=['sales'], as_of='2026-10-21', encoder=encoder)
    employee = search(new, state, '报价审批', roles=['employee'], as_of='2026-10-21', encoder=encoder)
    checks = {
        'baseline_three_modes': all(v['retrieval']['hits'] for v in before.values()),
        'stale_index_rejected': stale,
        'old_role_revoked': all(h['chunk']['document_id'] != changed['document_id'] for h in sales['hits']),
        'new_role_version2_visible': bool(employee['hits']) and all(h['chunk']['version'] == 2 and
            h['chunk']['document_id'] == changed['document_id'] for h in employee['hits']),
        'deleted_document_absent': all(c['document_id'] != removed['document_id'] for c in new['chunks']),
        'no_old_chunk_ids': not {c['chunk_id'] for c in old['chunks'] if c['document_id'] == changed['document_id']}
                           .intersection(c['chunk_id'] for c in new['chunks']),
    }
    if not all(checks.values()): raise ValueError('RAG demo failed: '+str(checks))
    write_new(output/'07-after.json', {'sales': sales, 'employee': employee})
    summary = {'kind': 'fixture-pipeline-check-not-semantic-quality', 'checks_passed': len(checks),
               **checks, 'changed_document': changed['document_id'], 'deleted_document': removed['document_id']}
    write_new(output/'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    demo = sub.add_parser('demo'); demo.add_argument('--output', type=Path, required=True)
    run = sub.add_parser('eval')
    run.add_argument('--index', type=Path, required=True); run.add_argument('--state', type=Path, required=True)
    run.add_argument('--cases', type=Path, default=Path('data/samples/m09-retrieval.json'))
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--mode', choices=('keyword', 'dense', 'hybrid'), default='hybrid')
    run.add_argument('--k', type=int, default=5); run.add_argument('--candidates', type=int, default=20)
    run.add_argument('--reranker', type=Path); encoder_options(run)
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo': report = run_demo(args.output)
        else:
            index = load_index(args.index)
            report = evaluate(index, args.state, load_cases(args.cases, index['tenant_id']),
                              make_encoder(args), args.output, args.mode, k=args.k, candidates=args.candidates,
                              reranker=LocalReranker(args.reranker, device=args.device) if args.reranker else None)
        print(_json(report.get('metrics', report)))
        return 1 if report.get('metrics', {}).get('errors', 0) else 0
    except (ValueError, OSError, ImportError, sqlite3.Error) as exc:
        print('M09 evaluation failed: '+type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
