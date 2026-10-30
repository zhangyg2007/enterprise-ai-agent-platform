"""Isolated PostgreSQL M11 backend. Runtime credentials belong to a trusted backend."""
from contextlib import contextmanager
from functools import wraps
import re

from .workflow_store import FixtureActor, FixtureStore
from .workflow_store import _identifier, _arguments, _json
from .workflow_graph import WorkflowLab, WorkflowBusy, _serde

IDENTITY_SQL = '''SELECT session_user AS login,current_user AS effective,c.*,
    (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb
     OR pg_has_role(session_user,d.datdba,'MEMBER')
     OR pg_has_role(session_user,n.nspowner,'MEMBER')) AS unsafe
    FROM workflow_auth.context() c JOIN pg_roles r ON r.rolname=session_user
    JOIN pg_database d ON d.datname=current_database()
    JOIN pg_namespace n ON n.nspname='workflow_lab' '''


def service_name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',value):
        raise ValueError('expected a local libpq service name')
    return value


def connection(service, *, checkpoint=False):
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(service=service_name(service), autocommit=True, row_factory=dict_row,
                           connect_timeout=5, prepare_threshold=0,
                           options='-c search_path='+('workflow_cp,pg_catalog' if checkpoint else 'pg_catalog'))


def identity(db):
    row=db.execute(IDENTITY_SQL).fetchone()
    if not row or row['login']!=row['effective'] or row['unsafe']:
        raise PermissionError('unmapped, switched or privileged runtime identity')
    return FixtureActor(row['tenant_id'],row['user_id'],row['employee_id'],row['role'])


def lab_identity(db):
    row=db.execute('SELECT lab_id,format_version FROM workflow_lab.metadata WHERE slot=1').fetchone()
    if not row or row['format_version']!=1 or not re.fullmatch(r'[a-f0-9]{32}',row['lab_id']):
        raise ValueError('unsupported PostgreSQL workflow lab')
    return row['lab_id']


class Row(dict):
    def __getitem__(self,key):
        return tuple(self.values())[key] if type(key) is int else super().__getitem__(key)


class Result:
    def __init__(self,cursor): self.cursor=cursor
    def fetchone(self):
        row=self.cursor.fetchone()
        return None if row is None else Row(row)
    def fetchall(self): return [Row(row) for row in self.cursor.fetchall()]


class FixedConnection:
    """Only the static SQL in FixtureStore is adapted, never model/user SQL."""
    def __init__(self,db): self.db=db
    def execute(self,sql,params=()):
        if not sql.startswith(('SELECT ','INSERT INTO ','UPDATE ')) or ';' in sql:
            raise ValueError('unsupported ledger SQL')
        sql=re.sub(r'\b(FROM|INTO|UPDATE) (resources|requests|customers|effects|audit)\b',
                   r'\1 workflow_lab.\2',sql)
        return Result(self.db.execute(sql.replace('?','%s'),params))


def _bound(method):
    @wraps(method)
    def call(self,actor,*args,**kwargs):
        if actor!=self.actor: raise PermissionError('actor differs from database login')
        return method(self,actor,*args,**kwargs)
    return call


class PgStore(FixtureStore):
    """Same deterministic contract; tenant advisory lock replaces BEGIN IMMEDIATE."""
    def __init__(self,service):
        self.service=service_name(service)
        with connection(self.service) as db:
            self.actor=identity(db); self.lab_id=lab_identity(db)

    @contextmanager
    def _transaction(self):
        with connection(self.service) as db,db.transaction():
            if identity(db)!=self.actor or lab_identity(db)!=self.lab_id:
                raise PermissionError('database identity/lab changed')
            db.execute('SELECT pg_advisory_xact_lock(%s,%s)',(7711,self.actor.tenant_id))
            yield FixedConnection(db)

    read_resource=_bound(FixtureStore.read_resource)
    get_request=_bound(FixtureStore.get_request)
    draft=_bound(FixtureStore.draft)
    decide=_bound(FixtureStore.decide)
    execute=_bound(FixtureStore.execute)
    effect_count=_bound(FixtureStore.effect_count)


def require_migrator(db):
    row=db.execute('SELECT current_user AS name,session_user AS login,current_database() AS db').fetchone()
    if row['name']!='m11_migrator' or row['login']!=row['name'] or not re.fullmatch(
            r'enterprise_workflows(?:_[a-z0-9]+)?',row['db']):
        raise PermissionError('dedicated workflow migration identity/database required')
    lab_identity(db)


def check_checkpoint_identity(db):
    row=db.execute('''SELECT session_user=current_user AND current_user='m11_cp'
        AND NOT (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb) AS safe
        FROM pg_roles WHERE rolname=session_user''').fetchone()
    if not row or not row['safe']: raise PermissionError('dedicated checkpoint identity required')


def setup_checkpoints(service):
    from langgraph.checkpoint.postgres import PostgresSaver
    with connection(service,checkpoint=True) as db:
        require_migrator(db)
        saver=PostgresSaver(db,serde=_serde())
        saver.jsonplus_serde=_serde()
        saver.setup()


class PgWorkflowLab(WorkflowLab):
    def __init__(self,service,checkpoint_service):
        self.store=PgStore(service)
        self.service=self.store.service
        self.checkpoint_service=service_name(checkpoint_service)

    @contextmanager
    def _locked(self):
        # Dedicated connection owns a session lock across graph/business commits.
        with connection(self.service) as db:
            if identity(db)!=self.store.actor or lab_identity(db)!=self.store.lab_id:
                raise PermissionError('database identity/lab changed')
            locked=db.execute('SELECT pg_try_advisory_lock(%s,%s) AS locked',(7712,1)).fetchone()['locked']
            if not locked: raise WorkflowBusy('workflow database is busy')
            try: yield
            finally: db.execute('SELECT pg_advisory_unlock(%s,%s)',(7712,1))

    def _authorize(self,actor,thread_id,*,manager=False):
        _identifier(thread_id)
        if actor!=self.store.actor: raise PermissionError('actor differs from database login')
        with self.store._transaction() as tx:
            row=tx.db.execute('SELECT * FROM workflow_lab.threads WHERE tenant=%s AND thread_id=%s',
                             (actor.tenant_id,thread_id)).fetchone()
        if row is None or (actor.role!='manager' and row['owner']!=actor.user_id):
            raise PermissionError('thread not permitted')
        if manager and (actor.role!='manager' or row['owner']==actor.user_id):
            raise PermissionError('independent manager required')
        self.store.get_request(actor,row['request_id'])
        return dict(row)

    @contextmanager
    def _graph(self,actor,row,*,fail_after_effect=False):
        from langgraph.checkpoint.postgres import PostgresSaver
        # Callers authorize before this checkpoint connection is opened.
        with connection(self.checkpoint_service,checkpoint=True) as db:
            check_checkpoint_identity(db)
            if lab_identity(db)!=self.store.lab_id: raise ValueError('checkpoint belongs to another lab')
            saver=PostgresSaver(db,serde=_serde())
            saver.jsonplus_serde=_serde()
            yield self._compile_graph(actor,row,saver,fail_after_effect=fail_after_effect)

    def start(self,actor,thread_id,idempotency_key,case,args):
        _identifier(thread_id); _identifier(idempotency_key)
        args=_arguments(case,args)
        if actor!=self.store.actor: raise PermissionError('actor differs from database login')
        binding=_json({'case':case,'args':args,'key':idempotency_key,
                       'employee_id':actor.employee_id,'role':actor.role})
        with self._locked():
            # A business draft can survive a failed registry commit; same input recovers it.
            with self.store._transaction() as tx:
                old=tx.db.execute('SELECT * FROM workflow_lab.threads WHERE tenant=%s AND thread_id=%s',
                                  (actor.tenant_id,thread_id)).fetchone()
            if old and (old['owner']!=actor.user_id or old['binding']!=binding):
                raise ValueError('thread already bound to different input')
            request=self.store.draft(actor,thread_id,idempotency_key,case,args)
            if request['request_id']!=thread_id: raise ValueError('key belongs to original thread')
            if old is None:
                with self.store._transaction() as tx:
                    tx.db.execute('INSERT INTO workflow_lab.threads VALUES(%s,%s,%s,%s,%s)',
                                  (actor.tenant_id,thread_id,actor.user_id,thread_id,binding))
            row=self._authorize(actor,thread_id)
            with self._graph(actor,row) as graph:
                config=self._config(row)
                if graph.get_state(config).created_at is None:
                    graph.invoke({'request_id':thread_id},config,durability='sync')
                return self._summary(actor,row,graph)


def seed_snapshot(service,obj,*,source):
    from .workflow_snapshot import normalize_snapshot
    snapshot=normalize_snapshot(obj)
    if source not in ('synthetic-demo','m02-reader-snapshot'): raise ValueError('invalid source label')
    with connection(service) as db,db.transaction():
        require_migrator(db)
        db.execute('SELECT pg_advisory_xact_lock(%s,%s)',(7711,snapshot['tenant']))
        old=db.execute('SELECT digest,source FROM workflow_lab.seeds WHERE tenant=%s',
                       (snapshot['tenant'],)).fetchone()
        if old:
            if old['digest']!=snapshot['digest'] or old['source']!=source:
                raise ValueError('tenant already seeded from different frozen input')
            return {'status':'unchanged','digest':snapshot['digest']}
        for row in snapshot['customers']:
            db.execute('INSERT INTO workflow_lab.customers VALUES(%s,%s)',row)
        for tenant,kind,rid,revision,value in snapshot['resources']:
            db.execute('INSERT INTO workflow_lab.resources VALUES(%s,%s,%s,%s,%s)',
                       (tenant,kind,rid,revision,_json(value)))
        db.execute('INSERT INTO workflow_lab.seeds VALUES(%s,%s,%s)',
                   (snapshot['tenant'],snapshot['digest'],source))
    return {'status':'seeded','digest':snapshot['digest']}
