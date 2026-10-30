"""Synthetic M11 business ledger. Actors are trusted lab inputs, not authentication."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .document_pipeline import _no_links


def _integer(value):
    if type(value) is not int or not 0 < value < 2**31:
        raise ValueError('expected positive integer below 2**31')
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('invalid request identifier/key')
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class FixtureActor:
    tenant_id: int
    user_id: int
    employee_id: int
    role: str

    def __post_init__(self):
        for value in (self.tenant_id, self.user_id, self.employee_id):
            _integer(value)
        if self.role not in ('sales', 'employee', 'procurement', 'manager'):
            raise ValueError('unsupported fixture role')


def _arguments(case, args):
    fields = {'quote': {'customer_id', 'product_id', 'quantity', 'discount_percent'},
              'leave': {'days'}, 'replenish': {'product_id'}}
    if case not in fields or not isinstance(args, dict) or set(args) != fields[case]:
        raise ValueError('unsupported case or unexpected/missing arguments')
    result = dict(args)
    for name, value in result.items():
        if name != 'discount_percent':
            _integer(value)
    if case == 'quote':
        discount = args['discount_percent']
        if not isinstance(discount, str) or not re.fullmatch(r'\d{1,2}(?:\.\d{1,2})?', discount):
            raise ValueError('discount must be a decimal string with at most two places')
        amount = Decimal(discount)
        if amount > 30:
            raise ValueError('discount exceeds 30 percent')
        result['discount_percent'] = format(amount.quantize(Decimal('.01')), '.2f')
    return result


class FixtureStore:
    """Connections are short-lived; each state transition uses one SQLite transaction."""

    def __init__(self, path):
        self.path = _no_links(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        with self._transaction() as db:
            if db.execute('PRAGMA application_id').fetchone()[0] != 0x4D3131:
                raise ValueError('not an M11 fixture database')

    @contextmanager
    def _transaction(self):
        # URI mode=rw prevents a typo/deleted path from creating another database.
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True,
                                     isolation_level=None, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _resource(db, actor, kind, resource_id):
        row = db.execute('SELECT revision,value FROM resources WHERE tenant=? AND kind=? AND id=?',
                         (actor.tenant_id, kind, resource_id)).fetchone()
        if row is None:
            raise ValueError('fixture resource unavailable')
        return {'revision': row['revision'], 'value': json.loads(row['value'])}

    def read_resource(self, actor, kind, resource_id):
        _integer(resource_id)
        allowed = (actor.role == 'manager' or
                   kind == 'catalog' and actor.role == 'sales' or
                   kind == 'leave' and actor.role == 'employee' and resource_id == actor.employee_id or
                   kind == 'stock' and actor.role == 'procurement')
        if kind not in ('catalog', 'leave', 'stock') or not allowed:
            raise PermissionError('resource not permitted')
        with self._transaction() as db:
            return self._resource(db, actor, kind, resource_id)

    @staticmethod
    def _visible(db, actor, request_id):
        _identifier(request_id)
        row = db.execute('SELECT * FROM requests WHERE tenant=? AND request_id=?',
                         (actor.tenant_id, request_id)).fetchone()
        if row is None or (actor.role != 'manager' and actor.user_id != row['owner']):
            raise PermissionError('request not permitted')
        return row

    @staticmethod
    def _public(row):
        return {'request_id': row['request_id'], 'case': row['case_name'],
                'status': row['status'], 'plan': json.loads(row['plan']),
                'owner': row['owner'], 'tenant_id': row['tenant']}

    def get_request(self, actor, request_id):
        with self._transaction() as db:
            return self._public(self._visible(db, actor, request_id))

    @staticmethod
    def _audit(db, actor, request_id, event):
        db.execute('INSERT INTO audit(tenant,request_id,user_id,event) VALUES(?,?,?,?)',
                   (actor.tenant_id, request_id, actor.user_id, event))

    def draft(self, actor, request_id, idempotency_key, case, args):
        _identifier(request_id)
        _identifier(idempotency_key)
        args = _arguments(case, args)
        if actor.role != {'quote': 'sales', 'leave': 'employee', 'replenish': 'procurement'}[case]:
            raise PermissionError('case not permitted')
        canonical = _json({'case': case, 'args': args, 'employee_id': actor.employee_id})
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self._transaction() as db:
            old = db.execute('SELECT * FROM requests WHERE tenant=? AND idem_key=?',
                             (actor.tenant_id, idempotency_key)).fetchone()
            if old is not None:
                if old['owner'] != actor.user_id:
                    raise PermissionError('key belongs to another owner')
                if old['input_hash'] != digest:
                    raise ValueError('idempotency key bound to different input')
                return self._public(old)
            kind = {'quote': 'catalog', 'leave': 'leave', 'replenish': 'stock'}[case]
            resource_id = actor.employee_id if case == 'leave' else args['product_id']
            snapshot = self._resource(db, actor, kind, resource_id)
            value = snapshot['value']
            if case == 'quote':
                if db.execute('SELECT 1 FROM customers WHERE tenant=? AND id=?',
                              (actor.tenant_id, args['customer_id'])).fetchone() is None:
                    raise ValueError('fixture customer unavailable')
                total = (Decimal(value['unit_price']) * args['quantity'] *
                         (1 - Decimal(args['discount_percent']) / 100)).quantize(
                             Decimal('.01'), rounding=ROUND_HALF_UP)
                plan = {**args, 'unit_price': value['unit_price'], 'total': format(total, '.2f')}
            elif case == 'leave':
                if args['days'] > value['remaining_days']:
                    raise ValueError('insufficient leave balance')
                plan = {**args, 'employee_id': actor.employee_id}
            else:
                gap = value['reorder_point'] - (value['on_hand'] - value['reserved'] + value['on_order'])
                if gap <= 0:
                    raise ValueError('no replenishment needed')
                plan = {**args, 'quantity': gap}
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (actor.tenant_id, request_id, actor.user_id, case, idempotency_key,
                        digest, kind, resource_id, _json(snapshot), _json(plan), 'pending', None))
            self._audit(db, actor, request_id, 'draft')
            return self._public(self._visible(db, actor, request_id))

    @staticmethod
    def _manager(actor, row):
        if actor.role != 'manager' or actor.user_id == row['owner']:
            raise PermissionError('independent manager required')

    def decide(self, actor, request_id, approved):
        if type(approved) is not bool:
            raise ValueError('approval must be boolean')
        with self._transaction() as db:
            row = self._visible(db, actor, request_id)
            self._manager(actor, row)
            if row['status'] != 'pending':
                raise ValueError('decision is immutable')
            status = 'approved' if approved else 'rejected'
            db.execute('UPDATE requests SET status=?,approver=? WHERE tenant=? AND request_id=?',
                       (status, actor.user_id, actor.tenant_id, request_id))
            self._audit(db, actor, request_id, status)
            return {'request_id': request_id, 'status': status}

    def execute(self, actor, request_id):
        with self._transaction() as db:
            row = self._visible(db, actor, request_id)
            self._manager(actor, row)
            if row['status'] == 'executed':
                effect = db.execute('SELECT receipt FROM effects WHERE tenant=? AND request_id=?',
                                    (actor.tenant_id, request_id)).fetchone()
                return {'status': 'executed', 'effect': json.loads(effect[0]), 'replayed': True}
            if row['status'] == 'conflict':
                return {'status': 'conflict', 'replayed': False}
            if row['status'] != 'approved':
                raise ValueError('execution requires approval')
            current = self._resource(db, actor, row['kind'], row['resource_id'])
            if current != json.loads(row['snapshot']):
                db.execute("UPDATE requests SET status='conflict' WHERE tenant=? AND request_id=?",
                           (actor.tenant_id, request_id))
                self._audit(db, actor, request_id, 'conflict')
                return {'status': 'conflict', 'replayed': False}
            plan = json.loads(row['plan'])
            value = current['value']
            if row['case_name'] == 'leave':
                if plan['days'] > value['remaining_days']:
                    raise ValueError('insufficient leave balance')
                value['remaining_days'] -= plan['days']
            elif row['case_name'] == 'replenish':
                value['on_order'] += plan['quantity']
            if row['case_name'] != 'quote':
                db.execute('UPDATE resources SET revision=revision+1,value=? WHERE tenant=? AND kind=? AND id=?',
                           (_json(value), actor.tenant_id, row['kind'], row['resource_id']))
            receipt = {'request_id': request_id, 'case': row['case_name'], **plan}
            db.execute('INSERT INTO effects VALUES(?,?,?)', (actor.tenant_id, request_id, _json(receipt)))
            db.execute("UPDATE requests SET status='executed' WHERE tenant=? AND request_id=?",
                       (actor.tenant_id, request_id))
            self._audit(db, actor, request_id, 'executed')
            return {'status': 'executed', 'effect': receipt, 'replayed': False}

    def effect_count(self, actor):
        """Tenant-scoped manager inspection for the synthetic lab."""
        if actor.role != 'manager':
            raise PermissionError('manager inspection required')
        with self._transaction() as db:
            return db.execute('SELECT count(*) FROM effects WHERE tenant=?', (actor.tenant_id,)).fetchone()[0]


def create_store(path):
    """Create a new, isolated teaching database without overwriting an existing path."""
    path = _no_links(path)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''
            PRAGMA application_id=5058865;
            PRAGMA foreign_keys=ON;
            CREATE TABLE resources(tenant INTEGER,kind TEXT,id INTEGER,revision INTEGER NOT NULL,
                value TEXT NOT NULL,PRIMARY KEY(tenant,kind,id));
            CREATE TABLE customers(tenant INTEGER,id INTEGER,PRIMARY KEY(tenant,id));
            CREATE TABLE requests(tenant INTEGER,request_id TEXT,owner INTEGER NOT NULL,case_name TEXT NOT NULL,
                idem_key TEXT NOT NULL,input_hash TEXT NOT NULL,kind TEXT NOT NULL,resource_id INTEGER NOT NULL,
                snapshot TEXT NOT NULL,plan TEXT NOT NULL,status TEXT NOT NULL,approver INTEGER,
                PRIMARY KEY(tenant,request_id),UNIQUE(tenant,idem_key),
                FOREIGN KEY(tenant,kind,resource_id) REFERENCES resources(tenant,kind,id));
            CREATE TABLE effects(tenant INTEGER,request_id TEXT,receipt TEXT NOT NULL,
                PRIMARY KEY(tenant,request_id),FOREIGN KEY(tenant,request_id) REFERENCES requests(tenant,request_id));
            CREATE TABLE audit(sequence INTEGER PRIMARY KEY,tenant INTEGER,request_id TEXT,user_id INTEGER,event TEXT,
                FOREIGN KEY(tenant,request_id) REFERENCES requests(tenant,request_id));
        ''')
        for tenant in (1, 2):
            for rid in (1, 2):
                db.execute('INSERT INTO customers VALUES(?,?)', (tenant, rid))
                db.execute('INSERT INTO resources VALUES(?,?,?,?,?)',
                           (tenant, 'leave', rid, 1, _json({'remaining_days': 15})))
            for kind, value in [('catalog', {'unit_price': '100.00'}),
                                ('stock', {'on_hand': 20, 'reserved': 5, 'on_order': 4, 'reorder_point': 30})]:
                db.execute('INSERT INTO resources VALUES(?,?,?,?,?)', (tenant, kind, 1, 1, _json(value)))
        db.commit()
    return FixtureStore(path)
