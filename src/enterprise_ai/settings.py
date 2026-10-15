"""Restricted .env format shared by the local teaching CLI and Compose."""
import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEYS = ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PORT',
        'POSTGRES_PASSWORD', 'LAB_A_PASSWORD', 'LAB_B_PASSWORD')


def create_env(path: Path) -> None:
    values = dict(POSTGRES_DB='enterprise_ai', POSTGRES_USER='lab_admin', POSTGRES_PORT='5432')
    for key in KEYS[3:]:
        values[key] = secrets.token_urlsafe(32)
    # O_EXCL protects existing credentials, including symlinks; owner-only on Unix.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write('# Generated locally; keep private.\n')
        for key in KEYS:
            handle.write(f'{key}={values[key]}\n')


def load_settings(path: Path = ROOT / '.env', environ=None) -> dict[str, str]:
    environ = os.environ if environ is None else environ
    values = {}
    for number, raw in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Z_]+)=([A-Za-z0-9_\-]*)', line)
        if not match or match[1] not in KEYS or match[1] in values:
            raise ValueError(f'Invalid or duplicate .env key at line {number}; use the generated format')
        values[match[1]] = match[2]
    for key in KEYS:
        if key in environ:
            values[key] = environ[key]
        if not values.get(key):
            raise ValueError(f'Missing setting: {key}')
    for key in ('POSTGRES_DB', 'POSTGRES_USER'):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', values[key]):
            raise ValueError(f'Invalid setting: {key}')
    if not values['POSTGRES_PORT'].isdigit() or not 1 <= int(values['POSTGRES_PORT']) <= 65535:
        raise ValueError('Invalid setting: POSTGRES_PORT')
    for key in KEYS[3:]:
        if not re.fullmatch(r'[A-Za-z0-9_\-]{32,128}', values[key]):
            raise ValueError(f'Invalid secret format: {key}; use 32–128 URL-safe characters')
    return values
