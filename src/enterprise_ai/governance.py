"""A bounded, single-writer file-event governance lab using only the standard library."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata
import uuid

from enterprise_ai.data import dataset_digest, validate_dataset


SCHEMA_VERSION = 1
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
EVENT_FIELDS = {'event_id', 'tenant_id', 'customer_id', 'version', 'op', 'payload'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('Non-finite JSON number')


def _parse(text):
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite)


def _bounded_bytes(path, maximum):
    with Path(path).open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('File exceeds size limit')
    return data


@contextmanager
def _lock(workspace):
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / '.lock'
    # Never remove a lock that this process did not create.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(_json({'pid': os.getpid()}))
        yield
    finally:
        path.unlink()


def _archive(workspace, digest, data):
    directory = workspace / 'raw'
    directory.mkdir(exist_ok=True)
    path = directory / (digest + '.jsonl')
    try:
        with path.open('xb') as stream:
            stream.write(data)
    except FileExistsError:
        pass
    if _digest(_bounded_bytes(path, MAX_FILE_BYTES)) != digest:
        raise ValueError('Raw archive digest mismatch; preserve and investigate the original')


def _setup(conn, seed_digest, tenants):
    statements = [
        'CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS batches (digest TEXT PRIMARY KEY, report TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, canonical TEXT NOT NULL, '
        'batch_digest TEXT NOT NULL, line_number INTEGER NOT NULL, outcome TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS entities (tenant_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, '
        'version INTEGER NOT NULL, deleted INTEGER NOT NULL, name TEXT, owner_employee_id INTEGER, '
        'PRIMARY KEY (tenant_id, customer_id))',
        'CREATE TABLE IF NOT EXISTS quarantine (batch_digest TEXT NOT NULL, line_number INTEGER NOT NULL, '
        'error_code TEXT NOT NULL, raw_text TEXT NOT NULL, PRIMARY KEY (batch_digest, line_number))',
    ]
    for statement in statements:
        conn.execute(statement)
    expected = {'schema_version': str(SCHEMA_VERSION), 'seed_digest': seed_digest,
                'tenants': _json(sorted(tenants))}
    stored = dict(conn.execute('SELECT key, value FROM metadata'))
    if stored and stored != expected:
        raise ValueError('Workspace seed or pipeline schema mismatch; use a separate workspace')
    if not stored:
        conn.executemany('INSERT INTO metadata VALUES (?, ?)', expected.items())


class _Invalid(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _text(value, maximum):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and not any(unicodedata.category(char) in {'Cc', 'Cs'} for char in value))


def _positive(value):
    return type(value) is int and 0 < value <= 2147483647


def _event(text, tenants, owners):
    try:
        event = _parse(text)
    except (ValueError, RecursionError):
        raise _Invalid('INVALID_JSON') from None
    if not isinstance(event, dict) or set(event) != EVENT_FIELDS:
        raise _Invalid('INVALID_EVENT')
    if not _text(event['event_id'], 128) or not event['event_id'].strip():
        raise _Invalid('INVALID_EVENT')
    if not all(_positive(event[key]) for key in ('tenant_id', 'customer_id', 'version')):
        raise _Invalid('INVALID_EVENT')
    if event['tenant_id'] not in tenants:
        raise _Invalid('UNKNOWN_TENANT')
    payload = event['payload']
    if event['op'] == 'delete':
        if payload is not None:
            raise _Invalid('INVALID_EVENT')
    elif event['op'] == 'upsert':
        if not isinstance(payload, dict) or set(payload) != {'name', 'owner_employee_id'}:
            raise _Invalid('INVALID_EVENT')
        if not _text(payload['name'], 200) or not payload['name'].strip() or not _positive(payload['owner_employee_id']):
            raise _Invalid('INVALID_EVENT')
        if (event['tenant_id'], payload['owner_employee_id']) not in owners:
            raise _Invalid('UNKNOWN_OWNER')
    else:
        raise _Invalid('INVALID_EVENT')
    return event


def _process(conn, lines, digest, tenants, owners):
    report = dict(batch_digest=digest, rows=len(lines), applied=0, duplicate=0,
                  stale=0, quarantined=0, cleaned=0)
    for number, line in enumerate(lines, 1):
        try:
            event = _event(line, tenants, owners)
            canonical = _json(event)
            prior = conn.execute('SELECT canonical FROM events WHERE event_id=?',
                                 (event['event_id'],)).fetchone()
            if prior is not None:
                if prior[0] != canonical:
                    raise _Invalid('EVENT_CONFLICT')
                report['duplicate'] += 1
                continue
            key = (event['tenant_id'], event['customer_id'])
            current = conn.execute('SELECT version FROM entities WHERE tenant_id=? AND customer_id=?', key).fetchone()
            if current and event['version'] == current[0]:
                # A valid event ID stays immutable even if its version cannot be applied.
                conn.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?)',
                             (event['event_id'], canonical, digest, number, 'quarantined'))
                raise _Invalid('VERSION_CONFLICT')
            outcome = 'stale' if current and event['version'] < current[0] else 'applied'
            conn.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?)',
                         (event['event_id'], canonical, digest, number, outcome))
            report[outcome] += 1
            if outcome == 'stale':
                continue
            deleted = event['op'] == 'delete'
            name = None if deleted else event['payload']['name'].strip()
            owner = None if deleted else event['payload']['owner_employee_id']
            if not deleted and name != event['payload']['name']:
                report['cleaned'] += 1
            conn.execute('INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?) '
                         'ON CONFLICT(tenant_id,customer_id) DO UPDATE SET version=excluded.version, '
                         'deleted=excluded.deleted, name=excluded.name, owner_employee_id=excluded.owner_employee_id',
                         (*key, event['version'], int(deleted), name, owner))
        except _Invalid as exc:
            conn.execute('INSERT INTO quarantine VALUES (?, ?, ?, ?)', (digest, number, exc.code, line))
            report['quarantined'] += 1
    conn.execute('INSERT INTO batches VALUES (?, ?)', (digest, _json(report)))
    return report


def _write_json(path, value):
    data = (_json(value) + '\n').encode('utf-8')
    with path.open('xb') as stream:
        stream.write(data)
    return _digest(data)


def _publish(conn, workspace, report):
    columns = ('tenant_id', 'customer_id', 'version', 'name', 'owner_employee_id')
    customers = [dict(zip(columns, row)) for row in conn.execute(
        'SELECT tenant_id, customer_id, version, name, owner_employee_id FROM entities '
        'WHERE deleted=0 ORDER BY tenant_id, customer_id')]
    metadata = dict(conn.execute('SELECT key,value FROM metadata'))
    counts = dict(conn.execute('SELECT tenant_id, count(*) FROM entities WHERE deleted=0 GROUP BY tenant_id'))
    summary = [{'tenant_id': tenant, 'active_customers': counts.get(tenant, 0)}
               for tenant in _parse(metadata['tenants'])]
    directory = workspace / 'snapshots' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    contents = {'customers.json': customers, 'summary.json': summary, 'report.json': report}
    manifest = {'schema_version': SCHEMA_VERSION, 'seed_digest': metadata['seed_digest'],
                'batch_digest': report['batch_digest'],
                'committed_batches': conn.execute('SELECT count(*) FROM batches').fetchone()[0],
                'files': {name: _write_json(directory / name, value) for name, value in contents.items()}}
    manifest_digest = _write_json(directory / 'manifest.json', manifest)
    pointer = {'manifest': (directory / 'manifest.json').relative_to(workspace).as_posix(),
               'sha256': manifest_digest}
    pending = workspace / ('CURRENT.' + uuid.uuid4().hex + '.tmp')
    try:
        _write_json(pending, pointer)
        os.replace(pending, workspace / 'CURRENT.json')
    finally:
        pending.unlink(missing_ok=True)


def run_batch(seed_path, input_path, workspace):
    """Commit a complete batch then publish a checked snapshot; retry repairs publication."""
    workspace = Path(workspace)
    with _lock(workspace):
        # Reject file-level errors before archiving or starting a state transaction.
        data = _bounded_bytes(input_path, MAX_FILE_BYTES)
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('Input must be UTF-8') from None
        byte_lines = data.split(b'\n')
        if data.endswith(b'\n'):
            byte_lines.pop()
        if not data:
            byte_lines = []
        if any(len(line[:-1] if line.endswith(b'\r') else line) > MAX_LINE_BYTES for line in byte_lines):
            raise ValueError('Input line exceeds 64 KiB')
        lines = text.split('\n')
        if text.endswith('\n'):
            lines.pop()
        if not text:
            lines = []
        lines = [line[:-1] if line.endswith('\r') else line for line in lines]
        seed = _parse(Path(seed_path).read_text(encoding='utf-8'))
        validate_dataset(seed)
        seed_hash = dataset_digest(seed)
        tenants = {row['id'] for row in seed['tables']['core.tenants']}
        owners = {(row['tenant_id'], row['id']) for row in seed['tables']['hcm.employees']}
        digest = _digest(data)
        _archive(workspace, digest, data)
        conn = sqlite3.connect(workspace / 'state.sqlite3', timeout=0)
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                _setup(conn, seed_hash, tenants)
                previous = conn.execute('SELECT report FROM batches WHERE digest=?', (digest,)).fetchone()
                report = _parse(previous[0]) if previous else _process(conn, lines, digest, tenants, owners)
            result = dict(report, unchanged=previous is not None)
            _publish(conn, workspace, result)
            return result
        finally:
            conn.close()


def inspect_workspace(workspace):
    """Read only the atomically published snapshot, verifying every recorded file hash."""
    workspace = Path(workspace)
    pointer = _parse((workspace / 'CURRENT.json').read_text(encoding='utf-8'))
    if not isinstance(pointer, dict):
        raise ValueError('Invalid snapshot pointer')
    relative = pointer.get('manifest')
    if not isinstance(relative, str) or not re.fullmatch(r'snapshots/[0-9a-f]{32}/manifest\.json', relative):
        raise ValueError('Invalid snapshot pointer')
    manifest_path = workspace / relative
    # Reject symlink escapes as well as traversal in the pointer.
    if not manifest_path.resolve().is_relative_to(workspace.resolve()):
        raise ValueError('Snapshot path escapes workspace')
    data = manifest_path.read_bytes()
    if _digest(data) != pointer.get('sha256'):
        raise ValueError('Manifest digest mismatch')
    manifest = _parse(data)
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != SCHEMA_VERSION
            or not isinstance(manifest.get('files'), dict)
            or set(manifest['files']) != {'customers.json', 'summary.json', 'report.json'}):
        raise ValueError('Invalid snapshot manifest')
    result = {'manifest': manifest}
    for name, digest in manifest['files'].items():
        path = manifest_path.parent / name
        if not path.resolve().is_relative_to(workspace.resolve()):
            raise ValueError('Snapshot file escapes workspace')
        data = path.read_bytes()
        if _digest(data) != digest:
            raise ValueError('Snapshot content digest mismatch: ' + name)
        result[name.removesuffix('.json')] = _parse(data)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('--seed', type=Path, required=True)
    run.add_argument('--input', type=Path, required=True)
    run.add_argument('--workspace', type=Path, required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (run_batch(args.seed, args.input, args.workspace) if args.command == 'run'
                  else inspect_workspace(args.workspace))
        if args.command == 'inspect':
            result = {key: value for key, value in result.items() if key != 'customers'}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, sqlite3.Error, RecursionError) as exc:
        # Raw input can be sensitive; never echo exception text or input rows.
        print(f'Governance operation failed ({type(exc).__name__}); check input, lock, seed and snapshot integrity.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
