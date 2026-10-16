"""Synthetic CRM change feed and an isolated, repeatable M03 acceptance lab."""
import argparse
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

from .data import dataset_digest, validate_dataset


def create_demo(seed_path: Path, output: Path) -> dict:
    """Create two small feeds; never overwrite an existing output directory."""
    seed = json.loads(Path(seed_path).read_text(encoding='utf-8'))
    validate_dataset(seed)
    customers = sorted(seed['tables']['crm.customers'], key=lambda r: (r['tenant_id'], r['id']))
    if seed['profile'] != 'tiny' or {(r['tenant_id'], r['id']) for r in customers} != {
            (tenant, cid) for tenant in (1, 2) for cid in range(1, 21)}:
        raise ValueError('demo requires a generated tiny seed with 20 customers per tenant')
    initial = [dict(event_id=f"crm-{r['tenant_id']}-{r['id']}-v1", tenant_id=r['tenant_id'],
                    customer_id=r['id'], version=1, op='upsert',
                    payload=dict(name=r['name'], owner_employee_id=r['owner_employee_id']))
               for r in customers]
    initial[0]['payload']['name'] = '  ' + initial[0]['payload']['name'] + '  '
    bad_tenant = copy.deepcopy(initial[0])
    bad_tenant.update(event_id='bad-tenant', tenant_id=99)
    bad_owner = copy.deepcopy(initial[0])
    bad_owner['event_id'] = 'bad-owner'
    bad_owner['payload']['owner_employee_id'] = 9999
    first = initial + [copy.deepcopy(initial[0]), '{', bad_tenant, bad_owner]

    update = copy.deepcopy(initial[0])
    update.update(event_id='crm-1-1-v2', version=2)
    update['payload']['name'] = '星桥客户一（更新）'
    deletion = dict(event_id='crm-1-2-delete-v2', tenant_id=1, customer_id=2,
                    version=2, op='delete', payload=None)
    late = copy.deepcopy(initial[0])
    late['event_id'] = 'late-crm-1-1-v1'
    id_conflict = copy.deepcopy(update)
    id_conflict['payload']['name'] = '不应覆盖的同 ID 内容'
    version_conflict = copy.deepcopy(update)
    version_conflict['event_id'] = 'conflicting-current-version'
    bad_version = copy.deepcopy(update)
    bad_version.update(event_id='bad-bool-version', version=True)
    changes = [update, deletion, late, id_conflict, version_conflict, bad_version,
               copy.deepcopy(initial[0])]

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for filename, rows in [('01-initial.jsonl', first), ('02-changes.jsonl', changes)]:
        with (output / filename).open('x', encoding='utf-8', newline='\n') as handle:
            for row in rows:
                handle.write((row if isinstance(row, str) else json.dumps(row, ensure_ascii=False)) + '\n')
    card = {'synthetic': True, 'seed_digest': dataset_digest(seed),
            'source': 'M02 generated CRM customers; injected teaching errors',
            'files': {'01-initial.jsonl': len(first), '02-changes.jsonl': len(changes)},
            'expected_active_customers': 39}
    with (output / 'data-card.json').open('x', encoding='utf-8') as handle:
        json.dump(card, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return card


def _require(condition, description):
    if not condition:
        raise ValueError('acceptance failed: ' + description)


def verify_demo(seed_path: Path, *, temp_parent: Path | None = None) -> dict:
    """Replays into a disposable workspace, independent of the user's ETL state."""
    from .governance import inspect_workspace, run_batch
    with tempfile.TemporaryDirectory(prefix='m03-check-', dir=temp_parent or Path.cwd()) as temp:
        root = Path(temp)
        create_demo(seed_path, root / 'inputs')
        warehouse = root / 'warehouse'
        first_path = root / 'inputs' / '01-initial.jsonl'
        changes_path = root / 'inputs' / '02-changes.jsonl'
        first = run_batch(seed_path, first_path, warehouse)
        _require({key: first[key] for key in ['rows', 'applied', 'duplicate', 'stale', 'quarantined', 'cleaned']}
                 == dict(rows=44, applied=40, duplicate=1, stale=0, quarantined=3, cleaned=1), 'initial counts')
        first_snapshot = inspect_workspace(warehouse)
        _require(len(first_snapshot['customers']) == 40, 'initial customers')
        _require(run_batch(seed_path, first_path, warehouse)['unchanged'], 'initial replay')
        second = run_batch(seed_path, changes_path, warehouse)
        _require({key: second[key] for key in ['rows', 'applied', 'duplicate', 'stale', 'quarantined', 'cleaned']}
                 == dict(rows=7, applied=2, duplicate=1, stale=1, quarantined=3, cleaned=0), 'increment counts')
        snapshot = inspect_workspace(warehouse)
        expected_summary = [{'tenant_id': 1, 'active_customers': 19},
                            {'tenant_id': 2, 'active_customers': 20}]
        _require(snapshot['summary'] == expected_summary, 'tenant summary and deletion propagation')
        by_key = {(r['tenant_id'], r['customer_id']): r for r in snapshot['customers']}
        _require((1, 2) not in by_key and (2, 2) in by_key, 'tenant-qualified deletion')
        _require(by_key[(1, 1)]['name'] == '星桥客户一（更新）' and by_key[(1, 1)]['version'] == 2,
                 'update and stale/conflict protection')
        _require(run_batch(seed_path, changes_path, warehouse)['unchanged'], 'increment replay')
        _require(inspect_workspace(warehouse)['customers'] == snapshot['customers'], 'replay content')
        with closing(sqlite3.connect(warehouse / 'state.sqlite3')) as connection:
            _require(connection.execute('SELECT count(*) FROM batches').fetchone()[0] == 2, 'batch count')
            _require(connection.execute('SELECT count(*) FROM quarantine').fetchone()[0] == 6, 'quarantine count')
        return {'status': 'passed', 'active_customers': len(by_key), 'summary': expected_summary,
                'checks': ['initial_quality', 'batch_idempotency', 'increment_quality',
                           'delete_propagation', 'tenant_boundary', 'late_and_conflict_protection'],
                'scope': 'synthetic file ETL and SQLite; no PostgreSQL or model calls'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create', help='create synthetic input files, refusing overwrite')
    create.add_argument('--seed', type=Path, required=True)
    create.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify', help='replay the lab in an isolated temporary directory')
    verify.add_argument('--seed', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = create_demo(args.seed, args.output) if args.command == 'create' else verify_demo(args.seed)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f'{type(error).__name__}: M03 demo failed; inspect input paths and dataset contract.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
