"""Run on Ubuntu before starting the lab. Never prints generated credentials."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from enterprise_ai.settings import create_env

if __name__ == '__main__':
    try:
        create_env(ROOT / '.env')
    except FileExistsError:
        print('.env already exists; left unchanged. Existing database passwords are not rotated.')
        raise SystemExit(1)
    print('Created .env with independent random passwords; keep this file private.')
