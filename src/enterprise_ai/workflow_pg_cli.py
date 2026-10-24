"""M11 PostgreSQL operations; credentials use private libpq service/pass files."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys
import traceback

from .ollama import strict_json_loads
from .workflow_cli import _display
from .workflow_graph import WorkflowBusy, InjectedWorkflowFailure
from .workflow_pg import PgWorkflowLab, connection, seed_snapshot, setup_checkpoints
from .workflow_snapshot import export_snapshot, write_snapshot


def parser():
    root=argparse.ArgumentParser(description=__doc__)
    root.add_argument('--diagnostic-log',help='opt-in private traceback file; new path only, never publish')
    commands=root.add_subparsers(dest='command',required=True)
    services=commands.add_parser('create-services')
    services.add_argument('--directory',required=True)
    services.add_argument('--port',type=int,required=True)
    services.add_argument('--database',default='enterprise_workflows')
    demo=commands.add_parser('demo-snapshot')
    demo.add_argument('--output',required=True); demo.add_argument('--tenant',type=int,choices=(1,2),default=1)
    for command in ('setup','seed','snapshot-export','start','inspect','approve','reject','retry'):
        child=commands.add_parser(command)
        child.add_argument('--service',required=True)
        if command=='setup': continue
        if command=='seed':
            child.add_argument('--input',required=True)
            child.add_argument('--source',required=True,choices=('synthetic-demo','m02-reader-snapshot'))
            continue
        if command=='snapshot-export':
            child.add_argument('--year',type=int,required=True); child.add_argument('--output',required=True)
            continue
        child.add_argument('--checkpoints',default='m11-cp'); child.add_argument('--thread',required=True)
        if command=='start':
            child.add_argument('--key',required=True)
            child.add_argument('--case',required=True,choices=('quote','leave','replenish'))
            child.add_argument('--arguments',required=True)
        if command=='approve': child.add_argument('--fail-after-effect',action='store_true')
    return root


def create_services(directory,port,database):
    if type(port) is not int or not 1<=port<=65535 or not re.fullmatch(
            r'enterprise_workflows(?:_[a-z0-9]+)?',database): raise ValueError('invalid database/port')
    folder=Path(directory); folder.mkdir(mode=0o700)
    profiles={'m11-migrate':'m11_migrator','m11-cp':'m11_cp'}
    profiles.update({f'm11-{t}-{r}':f'm11_{t}_{r}' for t in ('a','b')
                     for r in ('sales','employee','procurement','manager')})
    config=[]; passwords=[]; sql=[]
    for profile,login in profiles.items():
        secret=secrets.token_urlsafe(32)
        config.append(f'[{profile}]\nhost=127.0.0.1\nport={port}\ndbname={database}\nuser={login}\n')
        passwords.append(f'127.0.0.1:{port}:{database}:{login}:{secret}\n')
        # Both login and URL-safe secret come from fixed code/generator, never external SQL input.
        sql.append(f"ALTER ROLE {login} PASSWORD '{secret}';\n")
    for name,text in [('services.conf','\n'.join(config)),('pgpass',''.join(passwords)),
                      ('passwords.sql','BEGIN;\n'+''.join(sql)+'COMMIT;\n')]:
        fd=os.open(folder/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as handle: handle.write(text)


def private_diagnostic(path,exc):
    """Explicit private evidence, never overwrite an existing path or print payloads."""
    if not path: return 'not_requested'
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            handle.write('PRIVATE traceback; may contain sensitive values. Do not publish.\n')
            handle.write(''.join(traceback.format_exception(exc)))
        return 'saved_private_file'
    except (OSError,ValueError): return 'not_written_preserve_existing_files'


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if args.command=='create-services':
            create_services(args.directory,args.port,args.database)
            result={'status':'created','credentials':'private files; never publish'}
        elif args.command=='demo-snapshot':
            obj={'tenant':args.tenant,'employee':1,'year':2026,'customers':[1,2],
                 'catalog':[[1,'100.00']],'leave':[[1,'15.00']], 'stock':[[1,20,5,4,30]]}
            write_snapshot(args.output,obj); result={'status':'created','source':'synthetic-demo'}
        elif args.command=='setup':
            setup_checkpoints(args.service); result={'status':'checkpoint_setup_complete'}
        elif args.command=='seed':
            path=Path(args.input)
            if path.stat().st_size>4_000_000: raise ValueError('snapshot too large')
            result=seed_snapshot(args.service,strict_json_loads(path.read_text()),source=args.source)
        elif args.command=='snapshot-export':
            with connection(args.service) as db:
                # M02 returns tuples, while the M11 store uses dict rows.
                from psycopg.rows import tuple_row
                db.row_factory=tuple_row
                obj=export_snapshot(db,args.year)
            write_snapshot(args.output,obj); result={'status':'exported','source':'m02-reader-snapshot'}
        else:
            lab=PgWorkflowLab(args.service,args.checkpoints); actor=lab.store.actor
            if args.command=='start':
                if len(args.arguments)>2048: raise ValueError('arguments too large')
                result=lab.start(actor,args.thread,args.key,args.case,strict_json_loads(args.arguments))
            elif args.command in ('approve','reject'):
                result=lab.decide(actor,args.thread,args.command=='approve',
                                  fail_after_effect=getattr(args,'fail_after_effect',False))
            elif args.command=='retry': result=lab.retry(actor,args.thread)
            else: result=lab.inspect(actor,args.thread)
            result=_display(lab,actor,result)
            result['kind']='postgres-frozen-input-langgraph'
        print(json.dumps(result,sort_keys=True,allow_nan=False)); return 0
    except Exception as exc:
        if isinstance(exc,WorkflowBusy): error,code='database_busy',4
        elif isinstance(exc,InjectedWorkflowFailure): error,code='synthetic_after_effect_failure',5
        elif isinstance(exc,PermissionError): error,code='permission_denied',3
        elif isinstance(exc,(ValueError,TypeError,FileExistsError,FileNotFoundError)):
            error,code='invalid_input_or_state',2
        else: error,code='operation_failed',1
        diagnostic=private_diagnostic(args.diagnostic_log,exc)
        print(json.dumps({'error':error,'diagnostic':diagnostic}),file=sys.stderr); return code


if __name__=='__main__': raise SystemExit(main())
