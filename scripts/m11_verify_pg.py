"""Explicit server acceptance, never discovered/skipped by the offline suite.

Requires fresh demo-seeded tenant1 AND tenant2, all m11-a-* / m11-b-* services.
Writes only new derived requests/effects in the dedicated M11 database.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json

from enterprise_ai.workflow_graph import WorkflowBusy, InjectedWorkflowFailure
from enterprise_ai.workflow_pg import PgWorkflowLab, connection
from enterprise_ai.workflow_store import _identifier
from enterprise_ai.workflow_pg_cli import private_diagnostic


def require(test,message):
    if not test: raise ValueError(message)


def verify(run):
    _identifier(run)
    if len(run)>32: raise ValueError('run id too long')
    labs={role:PgWorkflowLab('m11-a-'+role,'m11-cp') for role in ('sales','employee','procurement','manager')}
    other=PgWorkflowLab('m11-b-manager','m11-cp')
    owner=labs['employee']; manager=labs['manager']; ma=manager.store.actor
    baseline=manager.store.effect_count(ma)
    require(baseline==0,'use a fresh demo-seeded tenant; preserve prior evidence')
    require(manager.store.read_resource(ma,'leave',1)['value']['remaining_days']==15,'expected demo leave15')
    for lab in [*labs.values(),other]: require(lab.store.lab_id==manager.store.lab_id,'services use different databases')
    specs=[('leave','employee',{'days':3}),('quote','sales',{'customer_id':1,'product_id':1,'quantity':3,'discount_percent':'15'}),
           ('replenish','procurement',{'product_id':1})]
    for case,role,args in specs:
        lab=labs[role]; rid=run+'-'+case
        paused=lab.start(lab.store.actor,rid,rid,case,args)
        require(paused['waiting_for_approval'],'must pause')
        require(manager.store.effect_count(ma)==specs.index((case,role,args)),'effect before approval')
        done=manager.decide(ma,rid,True)
        require(done['status']=='executed','approval execution failed')
        require(manager.retry(ma,rid)['receipt']==done['receipt'],'completed retry changed receipt')
    require(manager.store.read_resource(ma,'leave',1)['value']['remaining_days']==12,'leave arithmetic')
    require(manager.inspect(ma,run+'-quote')['receipt']['effect']['total']=='255.00','quote arithmetic')
    stock=manager.store.read_resource(ma,'stock',1)['value']
    require(stock['on_order']==15 and stock['on_hand']==20,'procurement arithmetic')
    denied=0
    for operation in (lambda:owner.decide(owner.store.actor,run+'-leave',True),
                      lambda:other.inspect(other.store.actor,run+'-leave')):
        try: operation()
        except PermissionError: denied+=1
    require(denied==2,'self/cross-tenant permissions')
    rejected=run+'-reject'
    owner.start(owner.store.actor,rejected,rejected,'leave',{'days':1})
    manager.decide(ma,rejected,False)
    require(manager.store.effect_count(ma)==3,'rejection wrote an effect')
    fault=run+'-fault'; owner.start(owner.store.actor,fault,fault,'leave',{'days':2})
    try: manager.decide(ma,fault,True,fail_after_effect=True)
    except InjectedWorkflowFailure: pass
    else: raise ValueError('expected synthetic failure')
    # New lab object/connection, same database checkpoint and immutable business receipt.
    restarted=PgWorkflowLab('m11-a-manager','m11-cp')
    require(restarted.retry(restarted.store.actor,fault)['receipt']['replayed'],'expected business receipt replay')
    require(manager.store.effect_count(ma)==4,'duplicate business effect')
    require(manager.store.read_resource(ma,'leave',1)['value']['remaining_days']==10,'double leave debit')
    with owner._locked():
        try: manager.inspect(ma,fault)
        except WorkflowBusy: pass
        else: raise ValueError('database graph lock missing')
    # Distinct DB connections, same frozen resource revision: only one may debit it.
    for suffix in ('race1','race2'):
        rid=run+'-'+suffix
        owner.store.draft(owner.store.actor,rid,rid,'leave',{'days':10})
        manager.store.decide(ma,rid,True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(lambda rid:manager.store.execute(ma,rid)['status'],
                               [run+'-race1',run+'-race2']))
    require(sorted(outcomes)==['conflict','executed'],'concurrent resource revision protection')
    # An exception after resource/effect/status writes must roll back the transaction.
    probe=run+'-rollback'; sales=labs['sales']
    sales.store.draft(sales.store.actor,probe,probe,'quote',specs[1][2])
    manager.store.decide(ma,probe,True)
    original=manager.store._audit
    def fail_audit(*args): raise RuntimeError('transaction rollback probe')
    manager.store._audit=fail_audit
    try:
        try: manager.store.execute(ma,probe)
        except RuntimeError: pass
        else: raise ValueError('expected rollback probe')
    finally: manager.store._audit=original
    require(manager.store.get_request(ma,probe)['status']=='approved','failed transaction changed status')
    require(manager.store.effect_count(ma)==5,'failed transaction leaked an effect')
    remaining=manager.store.read_resource(ma,'leave',1)['value']['remaining_days']
    require(remaining==0,'concurrent request leave arithmetic')
    # SQL-level identity/ACL/RLS checks, no model-supplied tenant or role.
    with connection('m11-a-employee') as db:
        db.execute("SELECT set_config('app.tenant_id','2',false)")
        row=db.execute('SELECT tenant_id FROM workflow_auth.context()').fetchone()
        require(row['tenant_id']==1,'session setting spoofed identity')
        require(not db.execute('SELECT 1 FROM workflow_lab.requests WHERE tenant=2').fetchone(),'RLS leak')
        for sql in ('SELECT * FROM workflow_auth.identities','CREATE TABLE workflow_cp.denied_probe(x integer)'):
            from psycopg.errors import InsufficientPrivilege
            try:
                with db.transaction(): db.execute(sql)
            except InsufficientPrivilege: pass
            else: raise ValueError('unexpected privilege')
    return {'status':'passed','run':run,'checks':['three_cases','pause_reopen','rejection','permissions',
             'failure_replay','single_effect','database_lock','concurrent_revision','atomic_rollback',
             'session_mapping','rls','acl'],'effect_count':5,'remaining_days':remaining}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--run',required=True)
    p.add_argument('--diagnostic-log',help='opt-in private traceback file, new path only')
    args=p.parse_args()
    try: print(json.dumps(verify(args.run),sort_keys=True))
    except Exception as exc:
        diagnostic=private_diagnostic(args.diagnostic_log,exc)
        p.exit(1,f'PostgreSQL acceptance failed ({type(exc).__name__}); diagnostic={diagnostic}.\n')
