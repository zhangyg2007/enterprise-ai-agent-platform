"""Reproducible update, revocation, deletion and rollback experiment (no model)."""
import argparse
import copy
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from .document_pipeline import export_chunks, ingest
from .document_samples import generate_corpus


def _save(path,value):
    with path.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,sort_keys=True,indent=2)
        stream.write('\n')


def _check(condition,message):
    # Deliberate runtime check: never disabled by python -O.
    if not condition:
        raise ValueError('demo check failed: '+message)


def run_demo(output):
    output = Path(output)
    output.mkdir(parents=True,exist_ok=False)
    sources = output/'sources'
    catalog = generate_corpus(sources,formats='md')
    state = output/'state.sqlite'

    def load(name,documents):
        path = output/(name+'-catalog.json')
        _save(path,{'schema_version':'m08-catalog-v1','documents':documents})
        return ingest(path,sources,state,output/(name+'-report.json'))

    def view(name,tenant=1,roles=None):
        path = output/(name+'.jsonl')
        report = export_chunks(state,path,tenant_id=tenant,roles=roles or ['sales'],as_of='2026-10-21')
        _save(output/(name+'-export.json'),report)
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

    baseline = load('01-baseline',catalog['documents'])
    _check(baseline['created']==30,'30 baseline documents')
    repeated = load('02-repeat',catalog['documents'])
    _check(repeated['unchanged']==30,'idempotent repeat')
    before = view('before-sales')
    tenant2_before = view('before-tenant2',tenant=2)
    changed = copy.deepcopy(catalog['documents'][0])
    source = sources/changed['source_path']
    source.write_bytes(source.read_bytes()+'\n更新说明：审批记录增加复核人字段，仅员工演示角色可见。\n'.encode())
    changed.update(version=2,roles=['employee'],sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    _check(load('03-update',[changed])['updated']==1,'higher version update')
    removed = catalog['documents'][1]
    deletion = {'tenant_id':removed['tenant_id'],'document_id':removed['document_id'],'version':2,'op':'delete'}
    _check(load('04-delete',[deletion])['deleted']==1,'explicit deletion')
    sales = view('after-sales')
    employee = view('after-employee',roles=['employee'])
    tenant2 = view('after-tenant2',tenant=2)
    affected = {changed['document_id'],removed['document_id']}
    _check(not affected.intersection(c['document_id'] for c in sales),'old sales role revoked and deletion hidden')
    _check(employee and all(c['document_id']==changed['document_id'] and c['version']==2 for c in employee),'employee sees only new version')
    _check(all(c['tenant_id']==1 for c in sales+employee) and tenant2_before==tenant2,'tenant isolation')
    _check(not {c['chunk_id'] for c in before}.intersection(c['chunk_id'] for c in employee),'new chunks replace old IDs')
    with closing(sqlite3.connect(state)) as db:
        heads_before = db.execute('SELECT * FROM heads ORDER BY tenant_id,document_id').fetchall()
        historical = db.execute('SELECT COUNT(*) FROM chunks WHERE tenant_id=1 AND document_id=? AND version=1',(changed['document_id'],)).fetchone()[0]
        versions = db.execute('SELECT COUNT(*) FROM versions').fetchone()[0]
    bad = copy.deepcopy(changed); bad.update(version=3,sha256='0'*64)
    failure = load('05-bad-hash',[bad])
    with closing(sqlite3.connect(state)) as db:
        heads_after = db.execute('SELECT * FROM heads ORDER BY tenant_id,document_id').fetchall()
    _check(failure['status']=='failed' and heads_before==heads_after,'failed batch preserves all heads')
    _check(versions==32 and historical>0,'immutable history retained')
    summary = {'schema_version':'m08-demo-v1','kind':'synthetic-fixture-not-production',
               'baseline_documents':30,'history_versions':versions,'checks_passed':7,
               'checks':['repeat_unchanged','old_role_and_deleted_hidden','new_role_version2',
                         'tenant_isolation','new_chunk_ids','failed_batch_no_head_change','history_retained'],
               'changed_document':changed['document_id'],'deleted_document':removed['document_id']}
    _save(output/'summary.json',summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_demo(args.output),ensure_ascii=False))
        return 0
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(str(exc),file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
