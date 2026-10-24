"""Synthetic M11 process-restart exercises; fixed actors are not API authentication."""
import argparse
import json
import sqlite3
import sys

from .ollama import strict_json_loads
from .workflow_graph import InjectedWorkflowFailure, WorkflowBusy, WorkflowLab, create_lab
from .workflow_store import FixtureActor


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('init', 'start', 'inspect', 'approve', 'reject', 'retry'):
        child = commands.add_parser(command)
        child.add_argument('--lab', required=True)
        if command == 'init':
            continue
        child.add_argument('--actor', required=True, choices=('sales', 'employee', 'procurement', 'manager'))
        child.add_argument('--tenant', type=int, choices=(1, 2), default=1)
        child.add_argument('--thread', required=True)
        if command == 'start':
            child.add_argument('--key', required=True)
            child.add_argument('--case', required=True, choices=('quote', 'leave', 'replenish'))
            child.add_argument('--arguments', required=True)
        if command == 'approve':
            child.add_argument('--fail-after-effect', action='store_true', help='synthetic recovery exercise only')
    return parser


def _actor(name, tenant):
    user, employee = {'sales': (1, 1), 'employee': (2, 1),
                      'procurement': (3, 3), 'manager': (4, 4)}[name]
    return FixtureActor(tenant, user, employee, name)


def _display(lab, actor, result):
    if actor.role == 'manager':
        result['effect_count'] = lab.store.effect_count(actor)
        case = result['case']
        plan = result['plan']
        kind = {'quote': 'catalog', 'leave': 'leave', 'replenish': 'stock'}[case]
        resource_id = plan['employee_id'] if case == 'leave' else plan['product_id']
        result['resources'] = {kind: lab.store.read_resource(actor, kind, resource_id)['value']}
    return result


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == 'init':
            lab = create_lab(args.lab)
            result = {'kind': 'synthetic-fixture-persistent-langgraph', 'status': 'created', 'lab': str(lab.path)}
        else:
            lab = WorkflowLab(args.lab)
            actor = _actor(args.actor, args.tenant)
            if args.command == 'start':
                if len(args.arguments) > 2048:
                    raise ValueError('arguments exceed lab limit')
                result = lab.start(actor, args.thread, args.key, args.case, strict_json_loads(args.arguments))
            elif args.command in ('approve', 'reject'):
                result = lab.decide(actor, args.thread, args.command == 'approve',
                                    fail_after_effect=getattr(args, 'fail_after_effect', False))
            elif args.command == 'retry':
                result = lab.retry(actor, args.thread)
            else:
                result = lab.inspect(actor, args.thread)
            result = _display(lab, actor, result)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except WorkflowBusy:
        error, code = 'lab_busy', 4
    except InjectedWorkflowFailure:
        error, code = 'synthetic_after_effect_failure', 5
    except PermissionError:
        error, code = 'permission_denied', 3
    except (ValueError, TypeError, FileExistsError, FileNotFoundError, sqlite3.IntegrityError):
        error, code = 'invalid_input_or_state', 2
    except Exception:
        # Do not expose database paths, checkpoint metadata or exception payloads.
        error, code = 'operation_failed_inspect_before_retry', 1
    print(json.dumps({'error': error, 'kind': 'synthetic-fixture-persistent-langgraph'}), file=sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
