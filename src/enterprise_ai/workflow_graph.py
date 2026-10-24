"""M11 synthetic persistent approval lab, with authorization outside the graph."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .document_pipeline import _no_links
from .workflow_store import FixtureStore, _arguments, _identifier, _json, create_store

CONTROL_ID = 0x4D313143
CHECKPOINT_ID = 0x4D313150


class WorkflowBusy(RuntimeError):
    """Another process is operating on this synthetic lab."""


class InjectedWorkflowFailure(RuntimeError):
    """Explicit teaching fault after the business transaction commits."""


class WorkflowState(TypedDict, total=False):
    request_id: str
    plan: dict
    status: str
    receipt: dict


def _connect(path):
    return sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, check_same_thread=False)


def _serde():
    # Built-in safe types (including Interrupt) only; no application classes/pickle.
    return JsonPlusSerializer(pickle_fallback=False, allowed_json_modules=[], allowed_msgpack_modules=[])


class WorkflowLab:
    """One OS lock serializes this small lab, including across independent processes.

    It intentionally serializes different threads too. This is a teaching CLI,
    not a distributed worker queue or an identity provider.
    """

    def __init__(self, path):
        self.path = _no_links(path)
        self.control = self.path / 'control.sqlite'
        self.checkpoint = self.path / 'checkpoints.sqlite'
        self.lock_path = self.path / 'operation.lock'
        for name in ('business.sqlite', 'control.sqlite', 'checkpoints.sqlite', 'operation.lock'):
            target = _no_links(self.path / name)
            if not target.is_file():
                raise FileNotFoundError(target)
        self.store = FixtureStore(self.path / 'business.sqlite')
        lab_ids = []
        for db_path, expected in ((self.path / 'business.sqlite', 0x4D3131),
                                  (self.control, CONTROL_ID), (self.checkpoint, CHECKPOINT_ID)):
            with closing(_connect(db_path)) as db:
                if db.execute('PRAGMA application_id').fetchone()[0] != expected:
                    raise ValueError('not an M11 persistent lab database')
                try:
                    metadata = db.execute('SELECT lab_id,format_version FROM m11_lab_metadata WHERE slot=1').fetchall()
                except sqlite3.DatabaseError as exc:
                    raise ValueError('missing lab identity; do not mix or patch database files') from exc
                if len(metadata) != 1 or metadata[0][1] != 1 or not isinstance(metadata[0][0], str):
                    raise ValueError('unsupported lab identity/format')
                lab_ids.append(metadata[0][0])
        if len(set(lab_ids)) != 1:
            raise ValueError('database files belong to different labs')
        self.lab_id = lab_ids[0]

    @contextmanager
    def _locked(self):
        with self.lock_path.open('r+b') as stream:
            acquired = False
            try:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as exc:
                    raise WorkflowBusy('lab is busy; retry after the active operation finishes') from exc
                yield
            finally:
                if acquired:
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _authorize(self, actor, thread_id, *, manager=False):
        _identifier(thread_id)
        with closing(_connect(self.control)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM threads WHERE tenant=? AND thread_id=?',
                             (actor.tenant_id, thread_id)).fetchone()
        if row is None or (actor.role != 'manager' and row['owner'] != actor.user_id):
            raise PermissionError('thread not permitted')
        if manager and (actor.role != 'manager' or row['owner'] == actor.user_id):
            raise PermissionError('independent manager required')
        # Business authorization must succeed before a checkpoint is deserialized.
        self.store.get_request(actor, row['request_id'])
        return dict(row)

    @contextmanager
    def _graph(self, actor, row, *, fail_after_effect=False):
        request_id = row['request_id']

        def prepare(state):
            request = self.store.get_request(actor, request_id)
            return {'request_id': request_id, 'plan': request['plan'], 'status': request['status']}

        def review(state):
            # No business side effect before interrupt: the node runs again on resume.
            approved = interrupt({'request_id': request_id, 'plan': state['plan']})
            if type(approved) is not bool:
                raise ValueError('approval must be boolean')
            current = self.store.get_request(actor, request_id)['status']
            expected = 'approved' if approved else 'rejected'
            if current == 'pending':
                self.store.decide(actor, request_id, approved)
            elif current != expected and not (approved and current in ('executed', 'conflict')):
                raise ValueError('decision is immutable')
            return {'status': expected}

        def execute(state):
            receipt = self.store.execute(actor, request_id)
            if fail_after_effect:
                raise InjectedWorkflowFailure('synthetic failure after business commit, before graph completion')
            return {'status': receipt['status'], 'receipt': receipt}

        builder = StateGraph(WorkflowState)
        for name, node in (('prepare', prepare), ('review', review), ('execute', execute)):
            builder.add_node(name, node)
        builder.add_edge(START, 'prepare')
        builder.add_edge('prepare', 'review')
        builder.add_conditional_edges('review', lambda s: 'execute' if s['status'] == 'approved' else END,
                                      ['execute', END])
        builder.add_edge('execute', END)
        with closing(_connect(self.checkpoint)) as db:
            saver = SqliteSaver(db, serde=_serde())
            saver.jsonplus_serde = _serde()
            yield builder.compile(checkpointer=saver)

    @staticmethod
    def _config(row):
        # Global checkpoint IDs are tenant-namespaced from trusted registry data.
        return {'configurable': {'thread_id': f"{row['tenant']}:{row['thread_id']}"}}

    def _summary(self, actor, row, graph):
        snapshot = graph.get_state(self._config(row))
        request = self.store.get_request(actor, row['request_id'])
        return {'kind': 'synthetic-fixture-persistent-langgraph', 'thread_id': row['thread_id'],
                'request_id': row['request_id'], 'case': request['case'], 'status': request['status'],
                'waiting_for_approval': bool(snapshot.interrupts) and request['status'] == 'pending',
                'next': list(snapshot.next),
                'plan': request['plan'], 'receipt': snapshot.values.get('receipt'),
                'graph_failed': any(task.error is not None for task in snapshot.tasks)}

    def start(self, actor, thread_id, idempotency_key, case, args):
        _identifier(thread_id)
        _identifier(idempotency_key)
        args = _arguments(case, args)
        binding = _json({'case': case, 'args': args, 'key': idempotency_key,
                         'employee_id': actor.employee_id, 'role': actor.role})
        with self._locked():
            with closing(_connect(self.control)) as db:
                db.row_factory = sqlite3.Row
                old = db.execute('SELECT * FROM threads WHERE tenant=? AND thread_id=?',
                                 (actor.tenant_id, thread_id)).fetchone()
                if old is not None:
                    if old['owner'] != actor.user_id:
                        raise PermissionError('thread not permitted')
                    if old['binding'] != binding:
                        raise ValueError('thread already bound to different input')
                request = self.store.draft(actor, thread_id, idempotency_key, case, args)
                if request['request_id'] != thread_id:
                    raise ValueError('idempotency key belongs to another thread; use its original ID')
                if old is None:
                    # A crash before this commit leaves only a pending business draft.
                    # Repeating start with the same key/input safely recovers that draft.
                    db.execute('INSERT INTO threads VALUES(?,?,?,?,?)',
                               (actor.tenant_id, thread_id, actor.user_id, thread_id, binding))
                    db.commit()
            row = self._authorize(actor, thread_id)
            with self._graph(actor, row) as graph:
                config = self._config(row)
                if graph.get_state(config).created_at is None:
                    graph.invoke({'request_id': thread_id}, config, durability='sync')
                return self._summary(actor, row, graph)

    def inspect(self, actor, thread_id):
        with self._locked():
            row = self._authorize(actor, thread_id)
            with self._graph(actor, row) as graph:
                return self._summary(actor, row, graph)

    def decide(self, actor, thread_id, approved, *, fail_after_effect=False):
        if type(approved) is not bool:
            raise ValueError('approval must be boolean')
        with self._locked():
            row = self._authorize(actor, thread_id, manager=True)
            status = self.store.get_request(actor, row['request_id'])['status']
            if status != 'pending' and approved != (status in ('approved', 'executed', 'conflict')):
                # LangGraph can retain an earlier resume value after a node failure.
                # Reject an opposite command before touching that paused checkpoint.
                raise ValueError('decision is immutable')
            with self._graph(actor, row, fail_after_effect=fail_after_effect) as graph:
                config = self._config(row)
                snapshot = graph.get_state(config)
                if not snapshot.interrupts:
                    status = self.store.get_request(actor, row['request_id'])['status']
                    if not snapshot.next and status in (('executed', 'conflict') if approved else ('rejected',)):
                        return self._summary(actor, row, graph)
                    raise ValueError('thread is not waiting for approval; inspect or retry it')
                graph.invoke(Command(resume=approved), config, durability='sync')
                return self._summary(actor, row, graph)

    def retry(self, actor, thread_id):
        with self._locked():
            row = self._authorize(actor, thread_id, manager=True)
            with self._graph(actor, row) as graph:
                config = self._config(row)
                snapshot = graph.get_state(config)
                if snapshot.interrupts:
                    status = self.store.get_request(actor, row['request_id'])['status']
                    if status not in ('approved', 'rejected', 'executed', 'conflict'):
                        raise ValueError('approval required; retry cannot supply approval')
                    # The business decision committed before the review checkpoint.
                    # Recover that immutable decision, never take a new one from retry input.
                    graph.invoke(Command(resume=status != 'rejected'), config, durability='sync')
                    return self._summary(actor, row, graph)
                if snapshot.created_at is None:
                    raise ValueError('start has not initialized this graph')
                if snapshot.next:
                    graph.invoke(None, config, durability='sync')
                return self._summary(actor, row, graph)


def create_lab(path):
    path = _no_links(path)
    path.mkdir(mode=0o700)
    create_store(path / 'business.sqlite')
    for name in ('control.sqlite', 'checkpoints.sqlite', 'operation.lock'):
        with (path / name).open('xb') as stream:
            if name == 'operation.lock':
                stream.write(b'1')
        os.chmod(path / name, 0o600)
    with closing(_connect(path / 'control.sqlite')) as db:
        db.execute(f'PRAGMA application_id={CONTROL_ID}')
        db.execute('CREATE TABLE threads(tenant INTEGER, thread_id TEXT, owner INTEGER NOT NULL, '
                   'request_id TEXT NOT NULL, binding TEXT NOT NULL, PRIMARY KEY(tenant,thread_id), '
                   'UNIQUE(tenant,request_id))')
        db.commit()
    with closing(_connect(path / 'checkpoints.sqlite')) as db:
        db.execute(f'PRAGMA application_id={CHECKPOINT_ID}')
        saver = SqliteSaver(db, serde=_serde())
        saver.jsonplus_serde = _serde()
        saver.setup()
    lab_id = uuid4().hex
    for name in ('business.sqlite', 'control.sqlite', 'checkpoints.sqlite'):
        with closing(_connect(path / name)) as db:
            db.execute('CREATE TABLE m11_lab_metadata(slot INTEGER PRIMARY KEY CHECK(slot=1), '
                       'lab_id TEXT NOT NULL,format_version INTEGER NOT NULL)')
            db.execute('INSERT INTO m11_lab_metadata VALUES(1,?,1)', (lab_id,))
            db.commit()
    return WorkflowLab(path)
