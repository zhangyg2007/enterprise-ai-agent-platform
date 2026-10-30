"""Freeze authorized M09 evidence and operator-supplied business snapshots."""
import argparse
import json
from pathlib import Path
import sys

from .four_arm_eval import canonical, freeze_from_m09
from .ollama import strict_json_loads
from .rag_index import load_index
from .rag_models import FixtureEncoder, LocalEncoder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=Path, required=True)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--fixture-encoder', action='store_true')
    mode.add_argument('--embedding', type=Path)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu',
                        help='must match the device recorded in the M09 index identity')
    args = parser.parse_args(argv)
    if args.output.exists(): parser.error('output must not exist')
    if args.fixture_encoder and args.device != 'cpu': parser.error('fixture encoder does not use cuda')
    try:
        if args.seed.stat().st_size > 8*1024*1024: raise ValueError('seed too large')
        seed = strict_json_loads(args.seed.read_text(encoding='utf-8'))
        encoder = FixtureEncoder() if args.fixture_encoder else LocalEncoder(args.embedding, device=args.device)
        snapshot = freeze_from_m09(seed, load_index(args.index), args.state, encoder)
        with args.output.open('xb') as stream: stream.write(canonical(snapshot))
        print(json.dumps({'status': 'created', 'kind': snapshot['kind'], 'cases': len(snapshot['cases'])}))
        return 0
    except Exception:
        print('freeze failed; check index freshness, authorized role, encoder and new output path', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
