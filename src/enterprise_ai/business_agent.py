"""Synthetic business proposal lab: model plan, M09 evidence, M11 human approval.

Only for a trusted local operator controlling all files and entry points. It does
not secure direct M11/M12 callers or provide a cross-database atomic commit.
"""
from contextlib import closing
from datetime import date
import hashlib
import json
import re
import sqlite3

from .agent import Principal, _request
from .document_pipeline import _no_links
from .ollama import strict_json_loads
from .rag_search import search, evidence_answer
from .workflow_store import FixtureActor, _arguments, _identifier

POLICIES = {'quote': ('sales', 'crm-', '报价审批', '报价折扣审批制度'),
            'leave': ('employee', 'hcm-', '休假申请', '员工休假申请审批制度'),
            'replenish': ('procurement', 'scm-', '补货规则', '采购补货审批制度')}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _lab_id(lab):
    value = getattr(lab, 'lab_id', None)
    if value is None:
        value = getattr(lab.store, 'lab_id', None)
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('invalid workflow lab identity')
    return value


def _evidence(actor, case, index, state, encoder, as_of):
    role, prefix, topic, query = POLICIES[case]
    if not isinstance(actor, FixtureActor) or actor.role != role:
        raise PermissionError('business role denied')
    if index.get('tenant_id') != actor.tenant_id:
        raise PermissionError('index tenant mismatch')
    principal = Principal(actor.tenant_id, 'a' if actor.tenant_id == 1 else 'b', (role,), as_of, actor.employee_id)
    result = search(index, state, query, roles=list(principal.roles), as_of=principal.as_of,
                    k=20, candidates=20, encoder=encoder)
    answer = evidence_answer(result)
    # evidence_answer has verified each chunk digest. Its citation omits the
    # title, so consult the matching validated retrieval hit for headingless PDFs.
    titles = {hit['chunk']['chunk_id']: hit['chunk']['title'] for hit in result['hits']}
    citations = [c for c in answer['citations'] if c['document_id'].startswith(prefix)
                 and isinstance(c['section'], str)
                 and (topic in c['section'] or (not c['section'] and topic in titles[c['chunk_id']]))]
    if not citations:
        raise ValueError('no authorized current policy evidence for this case')
    return citations


def prepare(lab, actor, thread_id, key, request, planner, index, state, encoder, *, as_of):
    """Plan before document retrieval; do not pass untrusted documents to planner."""
    _identifier(thread_id); _identifier(key); _request(request)
    if getattr(lab, 'format_version', 1) != 2: raise ValueError('guarded SQLite lab required')
    if not isinstance(actor, FixtureActor): raise PermissionError('trusted actor required')
    plan = planner(request)
    if not isinstance(plan, dict) or set(plan) != {'case', 'arguments'}:
        raise ValueError('invalid model plan')
    case = plan['case']
    if case in ('clarify', 'refuse'):
        if plan['arguments'] != {}: raise ValueError('invalid clarification')
        return {'status': case, 'kind': 'synthetic-fixture-business-agent'}
    if case not in POLICIES or actor.role != POLICIES[case][0]:
        raise PermissionError('business role denied')
    arguments = _arguments(case, plan['arguments'])
    lab_id = _lab_id(lab)
    citations = _evidence(actor, case, index, state, encoder, as_of)
    context = {'kind': 'synthetic-fixture-business-agent',
            'thread_id': thread_id, 'idempotency_key': key, 'lab_id': lab_id, 'case': case, 'arguments': arguments,
            'actor': {'tenant_id': actor.tenant_id, 'user_id': actor.user_id,
                      'employee_id': actor.employee_id, 'role': actor.role},
            'as_of': as_of, 'index_digest': index['digest'],
            'citations': citations, 'citations_sha256': _hash(citations)}
    if getattr(lab, 'format_version', 1) == 2:
        from .guarded_workflow import register
        from .workflow_graph import _POLICY_GUARD
        register(lab, actor, context)
        workflow = lab.start(actor, thread_id, key, case, arguments, _policy_guard=_POLICY_GUARD)
    else:
        workflow = lab.start(actor, thread_id, key, case, arguments)
    return {**context, 'status': workflow['status'], 'workflow': workflow}


def decide(lab, manager, context, approved, index, state, encoder):
    """Recheck the original role's evidence before a pending approval; M11 decides."""
    if not isinstance(manager, FixtureActor) or manager.role != 'manager':
        raise PermissionError('independent manager required')
    if type(approved) is not bool or not isinstance(context, dict): raise ValueError('invalid context')
    if getattr(lab, 'format_version', 1) != 2: raise ValueError('guarded SQLite lab required')
    if context.get('lab_id') != _lab_id(lab): raise ValueError('context belongs to another workflow lab')
    guarded = getattr(lab, 'format_version', 1) == 2
    if guarded:
        from .guarded_workflow import verify
        from .workflow_graph import _POLICY_GUARD
        verify(lab, context)

    def invoke(method, *args):
        return method(*args, _policy_guard=_POLICY_GUARD) if guarded else method(*args)

    actor = FixtureActor(**context['actor'])
    if actor.tenant_id != manager.tenant_id or actor.user_id == manager.user_id:
        raise PermissionError('independent manager in same tenant required')
    thread = context['thread_id']; _identifier(thread)
    case = context['case']; arguments = _arguments(case, context['arguments'])
    current = lab.store.get_request(manager, thread)
    if (current['owner'] != actor.user_id or current['case'] != case
            or any(current['plan'].get(k) != v for k, v in arguments.items())):
        raise ValueError('context does not match persisted draft')
    if current['status'] in ('pending', 'approved') and approved:
        if index.get('digest') != context['index_digest']:
            raise ValueError('policy index changed; make a new draft')
        # M08 ingest also takes BEGIN IMMEDIATE. Keep this reserved writer lock
        # from freshness check until the M11 decision/effect returns. This is
        # a local cooperative lock, not a distributed transaction.
        path = _no_links(state).resolve(strict=True)
        with closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=10,
                                     isolation_level=None)) as guard:
            guard.execute('BEGIN IMMEDIATE')
            try:
                citations = _evidence(actor, case, index, state, encoder, context['as_of'])
                if _hash(citations) != context['citations_sha256'] or _hash(context['citations']) != context['citations_sha256']:
                    raise ValueError('policy evidence changed; make a new draft')
                current_citations = _evidence(actor, case, index, state, encoder, date.today().isoformat())
                if _hash(current_citations) != context['citations_sha256']:
                    raise ValueError('policy evidence is no longer current; make a new draft')
                if current['status'] == 'approved':
                    return invoke(lab.retry, manager, thread)
                return invoke(lab.decide, manager, thread, approved)
            finally:
                guard.rollback()
    if approved and current['status'] in ('executed', 'conflict'):
        # A side effect or conflict may have committed before graph checkpointing.
        # Retry reads the existing result without creating a new effect.
        return invoke(lab.retry, manager, thread)
    return invoke(lab.decide, manager, thread, approved)


class OllamaBusinessPlanner:
    """Local model proposes fields only. Server revalidates roles and arguments."""
    def __init__(self, client, model):
        self.client, self.model = client, model

    def __call__(self, request):
        _request(request)
        schema = {'type': 'object', 'properties': {
            'case': {'type': 'string', 'enum': ['quote', 'leave', 'replenish', 'clarify', 'refuse']},
            'arguments': {'type': 'object'}}, 'required': ['case', 'arguments'], 'additionalProperties': False}
        system = ('只输出JSON，不执行操作。quote字段customer_id,product_id,quantity均为正整数，'
                  'discount_percent为十进制字符串；leave只接受days正整数；replenish只接受product_id正整数。'
                  '信息不足用clarify和空arguments；越权或任意SQL用refuse和空arguments。'
                  '禁止输出租户、用户、角色、审批决定。')
        raw = self.client.chat(self.model, [{'role':'system','content':system}, {'role':'user','content':request}],
                               format_schema=schema, options={'temperature':0,'num_predict':192}, think=False)
        return strict_json_loads(raw['message']['content'])
