"""Private server-side credential bindings for the M12 single-process lab."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

from .document_pipeline import _no_links
from .ollama import strict_json_loads
from .workflow_store import FixtureActor


def validate_config(config):
    if not isinstance(config, dict) or config.get('mode') not in ('fixture', 'postgres'):
        raise ValueError('explicit fixture or postgres mode required')
    fixture = config['mode'] == 'fixture'
    if set(config) != {'mode', 'principals', 'lab' if fixture else 'checkpoint_service'}:
        raise ValueError('unexpected or missing server configuration')
    if fixture:
        if not isinstance(config['lab'], str) or not Path(config['lab']).is_absolute():
            raise ValueError('absolute fixture lab path required')
        _no_links(config['lab'])
    else:
        from .workflow_pg import service_name
        service_name(config['checkpoint_service'])
    principals = config['principals']
    if not isinstance(principals, list) or not 1 <= len(principals) <= 100:
        raise ValueError('expected 1..100 principals')
    seen = set()
    for principal in principals:
        if not isinstance(principal, dict) or set(principal) != {'token_sha256', 'actor' if fixture else 'service'}:
            raise ValueError('invalid principal binding')
        digest = principal['token_sha256']
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest) or digest in seen:
            raise ValueError('duplicate or invalid token digest')
        seen.add(digest)
        if fixture:
            actor = principal['actor']
            if not isinstance(actor, dict) or set(actor) != {'tenant_id', 'user_id', 'employee_id', 'role'}:
                raise ValueError('invalid trusted actor')
            FixtureActor(**actor)
        else:
            service_name(principal['service'])
    return deepcopy(config)


def load_config(path):
    path = _no_links(path)
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise ValueError('configuration must be owner-only (chmod 600)')
    if path.stat().st_size > 65536:
        raise ValueError('configuration too large')
    return validate_config(strict_json_loads(path.read_text(encoding='utf-8')))


def _write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def init_fixture_config(directory, lab):
    from .workflow_graph import WorkflowLab
    lab = WorkflowLab(lab)
    return _init(directory, lab.path)


def _init(directory, lab=None):
    directory = _no_links(directory)
    directory.mkdir(mode=0o700)
    config = {'mode': 'fixture' if lab else 'postgres', 'principals': []}
    config.update({'lab': str(lab)} if lab else {'checkpoint_service': 'm11-cp'})
    credentials = {}
    for tenant in (1, 2):
        for role, user, employee in (('sales', 1, 1), ('employee', 2, 1), ('procurement', 3, 3), ('manager', 4, 4)):
            token = secrets.token_urlsafe(32)
            credentials[f't{tenant}_{role}'] = token
            principal = {'token_sha256': hashlib.sha256(token.encode()).hexdigest()}
            principal.update({'actor': {'tenant_id': tenant, 'user_id': user, 'employee_id': employee, 'role': role}}
                             if lab else {'service': f"m11-{'a' if tenant == 1 else 'b'}-{role}"})
            config['principals'].append(principal)
    validate_config(config)
    _write_private(directory / 'service.json', config)
    _write_private(directory / 'credentials.json', credentials)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    fixture = sub.add_parser('init-fixture')
    fixture.add_argument('--directory', required=True)
    fixture.add_argument('--lab', required=True)
    pg = sub.add_parser('init-postgres')
    pg.add_argument('--directory', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'init-fixture':
            init_fixture_config(args.directory, args.lab)
        else:
            _init(args.directory)
    except (ValueError, OSError):
        parser.exit(1, 'configuration creation failed; use an existing lab and a new private directory\n')
    print('{"status":"created","secrets_printed":false}')


if __name__ == '__main__':
    main()
