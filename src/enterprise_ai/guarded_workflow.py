"""Opt-in, new-only guarded SQLite teaching labs; not a production identity system."""
from contextlib import closing

from .workflow_graph import WorkflowLab, create_lab, _connect
from .workflow_store import _identifier, _json

FIELDS = ('lab_id', 'thread_id', 'idempotency_key', 'case', 'arguments', 'actor',
          'as_of', 'index_digest', 'citations_sha256')


def create_guarded_lab(path):
    """Create a separate lab; never convert or overwrite an existing lab."""
    lab = create_lab(path)
    with lab._locked():
        for name in ('business.sqlite', 'control.sqlite', 'checkpoints.sqlite'):
            with closing(_connect(lab.path/name)) as db:
                if name == 'control.sqlite':
                    db.execute('CREATE TABLE policy_guard_metadata(slot INTEGER PRIMARY KEY CHECK(slot=1), version INTEGER NOT NULL)')
                    db.execute('INSERT INTO policy_guard_metadata VALUES(1,1)')
                    db.execute('CREATE TABLE policy_guard_bindings(tenant INTEGER NOT NULL,thread_id TEXT NOT NULL,'
                               'idempotency_key TEXT NOT NULL,binding TEXT NOT NULL,'
                               'PRIMARY KEY(tenant,thread_id),UNIQUE(tenant,idempotency_key))')
                db.execute('UPDATE m11_lab_metadata SET format_version=2 WHERE slot=1')
                db.commit()
    # Partial initialization produces mismatched versions and fails closed.
    return WorkflowLab(path)


def _binding(context):
    if not isinstance(context, dict) or any(field not in context for field in FIELDS):
        raise ValueError('missing guarded proposal binding')
    _identifier(context['thread_id']); _identifier(context['idempotency_key'])
    return _json({field: context[field] for field in FIELDS})


def register(lab, actor, context):
    """Freeze the trusted proposal before its first business draft is created."""
    if getattr(lab, 'format_version', 1) != 2: raise ValueError('guarded lab required')
    if context['lab_id'] != lab.lab_id or context['actor']['tenant_id'] != actor.tenant_id:
        raise ValueError('guarded lab/tenant mismatch')
    value = _binding(context)
    with lab._locked():
        with closing(_connect(lab.control)) as db:
            old = db.execute('SELECT binding FROM policy_guard_bindings WHERE tenant=? AND thread_id=?',
                             (actor.tenant_id, context['thread_id'])).fetchone()
            if old is None:
                db.execute('INSERT INTO policy_guard_bindings VALUES(?,?,?,?)',
                           (actor.tenant_id, context['thread_id'], context['idempotency_key'], value))
                db.commit()
            elif old[0] != value:
                raise ValueError('guarded proposal binding is immutable')


def verify(lab, context):
    if getattr(lab, 'format_version', 1) != 2: raise ValueError('guarded lab required')
    value = _binding(context)
    with closing(_connect(lab.control)) as db:
        row = db.execute('SELECT binding FROM policy_guard_bindings WHERE tenant=? AND thread_id=?',
                         (context['actor']['tenant_id'], context['thread_id'])).fetchone()
    if row is None or row[0] != value:
        raise ValueError('guarded proposal binding changed or is missing')
