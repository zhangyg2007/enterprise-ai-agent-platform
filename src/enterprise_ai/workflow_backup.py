"""Consistent, new-directory-only backups of the three M11 SQLite lab databases."""
import argparse
from contextlib import closing
import hashlib
import json
import os
import re
import shutil
import sqlite3

from .document_pipeline import _no_links
from .ollama import strict_json_loads
from .workflow_graph import WorkflowLab

FILES = ('business.sqlite', 'control.sqlite', 'checkpoints.sqlite')
APP_IDS = (0x4D3131, 0x4D313143, 0x4D313150)


def _hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _verify_database(path, expected_id, lab_id, lab_format=1):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('database integrity failure')
        if db.execute('PRAGMA application_id').fetchone()[0] != expected_id:
            raise ValueError('wrong database format')
        if db.execute('SELECT lab_id,format_version FROM m11_lab_metadata WHERE slot=1').fetchall() != [(lab_id, lab_format)]:
            raise ValueError('mixed lab identity')
        if lab_format == 2 and expected_id == APP_IDS[1]:
            try:
                if db.execute('SELECT version FROM policy_guard_metadata WHERE slot=1').fetchall() != [(1,)]:
                    raise ValueError('invalid guarded lab metadata')
                db.execute('SELECT tenant,thread_id,idempotency_key,binding FROM policy_guard_bindings LIMIT 0')
            except sqlite3.DatabaseError as exc:
                raise ValueError('missing guarded lab metadata') from exc


def backup_lab(source, target):
    lab = WorkflowLab(source)
    target = _no_links(target)
    if target.exists():
        raise FileExistsError(target)
    with lab._locked():
        target.mkdir(mode=0o700)
        for name, app_id in zip(FILES, APP_IDS):
            destination = target / name
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            with closing(sqlite3.connect((lab.path/name).as_uri()+'?mode=ro', uri=True)) as src:
                with closing(sqlite3.connect(destination)) as dst:
                    src.backup(dst)
            _verify_database(destination, app_id, lab.lab_id, lab.format_version)
        manifest = {'format': 1, 'kind': 'm11-sqlite-backup', 'lab_id': lab.lab_id,
                    'files': {name: _hash(target/name) for name in FILES}}
        if lab.format_version == 2:
            manifest['lab_format'] = 2
        # Manifest is the completion marker, written only after all three snapshots pass.
        with (target / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, indent=2)
        os.chmod(target / 'manifest.json', 0o600)
    return manifest


def restore_lab(source, target):
    source, target = _no_links(source), _no_links(target)
    if target.exists():
        raise FileExistsError(target)
    manifest_path = _no_links(source/'manifest.json')
    if manifest_path.stat().st_size > 4096:
        raise ValueError('manifest too large')
    manifest = strict_json_loads(manifest_path.read_text(encoding='utf-8'))
    base_fields = {'format', 'kind', 'lab_id', 'files'}
    if (not isinstance(manifest, dict) or set(manifest) not in (base_fields, base_fields | {'lab_format'})
            or type(manifest['format']) is not int or manifest['format'] != 1
            or manifest['kind'] != 'm11-sqlite-backup'
            or not isinstance(manifest['lab_id'], str) or not re.fullmatch('[a-f0-9]{32}', manifest['lab_id'])
            or not isinstance(manifest['files'], dict) or set(manifest['files']) != set(FILES)
            or ('lab_format' in manifest and (type(manifest['lab_format']) is not int or manifest['lab_format'] != 2))):
        raise ValueError('unsupported backup manifest')
    lab_format = manifest.get('lab_format', 1)
    for name, app_id in zip(FILES, APP_IDS):
        path = _no_links(source/name)
        if _hash(path) != manifest['files'][name]:
            raise ValueError('backup checksum mismatch')
        _verify_database(path, app_id, manifest['lab_id'], lab_format)
    target.mkdir(mode=0o700)
    for name in FILES:
        with (target/name).open('xb') as dst, (source/name).open('rb') as src:
            os.chmod(target/name, 0o600)
            shutil.copyfileobj(src, dst)
        if _hash(target/name) != manifest['files'][name]:
            raise ValueError('backup changed during restore; do not use partial target')
    with (target/'operation.lock').open('xb') as stream:
        stream.write(b'1')
    os.chmod(target/'operation.lock', 0o600)
    restored = WorkflowLab(target)
    return {'status': 'restored', 'lab_id': restored.lab_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('backup', 'restore'))
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    try:
        result = (backup_lab if args.command == 'backup' else restore_lab)(args.source, args.target)
    except Exception:
        parser.exit(1, 'backup/restore failed; retain source and inspect partial target; use a new target\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
