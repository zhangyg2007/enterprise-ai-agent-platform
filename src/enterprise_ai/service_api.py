"""M12 authenticated HTTP boundary; single worker, local teaching deployment."""
from collections import deque
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .service_config import load_config, validate_config
from .workflow_graph import WorkflowBusy, WorkflowLab
from .workflow_store import FixtureActor, _arguments

LOG = logging.getLogger('enterprise_ai.http')
Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')]


class StartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    thread_id: Identifier
    idempotency_key: Identifier
    case: Literal['quote', 'leave', 'replenish']
    arguments: dict


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approved: StrictBool


class HttpBoundary:
    """Bound streamed input before JSON parsing; never log user-controlled fields."""
    def __init__(self, app, counters, counter_lock):
        self.app, self.counters, self.lock = app, counters, counter_lock

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request_id, started, status = uuid4().hex, time.monotonic(), 503
        scope.setdefault('state', {})['request_id'] = request_id
        response_started = False

        async def traced_send(message):
            nonlocal status, response_started
            if message['type'] == 'http.response.start':
                response_started, status = True, message['status']
                message['headers'] = list(message.get('headers', [])) + [(b'x-request-id', request_id.encode()),
                    (b'cache-control', b'no-store')]
            await send(message)

        try:
            body = bytearray()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                if len(body) + len(chunk) > 8192:
                    return await JSONResponse({'error': 'body_too_large'}, status_code=413)(scope, receive, traced_send)
                body.extend(chunk)
                if not message.get('more_body', False):
                    break
            consumed = False

            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await receive()

            await self.app(scope, replay, traced_send)
        except Exception:
            if response_started:
                raise
            await JSONResponse({'error': 'service_unavailable_inspect_before_retry'}, status_code=503)(scope, receive, traced_send)
        finally:
            with self.lock:
                self.counters['requests'] += 1
                self.counters['server_errors'] += int(status >= 500)
                self.counters['denied'] += int(status in (401, 403, 429))
            LOG.info(json.dumps({'request_id': request_id, 'status': status,
                                'actor': scope.get('state', {}).get('audit_actor'),
                                'operation': getattr(scope.get('route'), 'name', 'unmatched'),
                                'duration_ms': round((time.monotonic() - started)*1000, 2)}))


def create_app(config, *, requests_per_minute=60):
    config = validate_config(config)
    if type(requests_per_minute) is not int or not 1 <= requests_per_minute <= 10000:
        raise ValueError('invalid request limit')
    app = FastAPI(title='M12 workflow teaching API', docs_url=None, redoc_url=None, openapi_url=None)
    counters = {'requests': 0, 'server_errors': 0, 'denied': 0}
    counter_lock, rate_lock = threading.Lock(), threading.Lock()
    buckets = {p['token_sha256']: deque() for p in config['principals']}
    app.add_middleware(HttpBoundary, counters=counters, counter_lock=counter_lock)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'error': 'invalid_request'}, status_code=422)

    def session(request: Request, authorization: Annotated[str | None, Header()] = None):
        if not authorization or len(authorization) > 256 or not authorization.startswith('Bearer '):
            raise HTTPException(401, 'authentication_required', headers={'WWW-Authenticate': 'Bearer'})
        token = authorization[7:]
        digest = hashlib.sha256(token.encode()).hexdigest()
        principal = next((p for p in config['principals'] if hmac.compare_digest(p['token_sha256'], digest)), None)
        if principal is None:
            raise HTTPException(401, 'authentication_required', headers={'WWW-Authenticate': 'Bearer'})
        now = time.monotonic()
        with rate_lock:
            bucket = buckets[digest]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= requests_per_minute:
                raise HTTPException(429, 'rate_limit', headers={'Retry-After': '60'})
            bucket.append(now)
        try:
            if config['mode'] == 'fixture':
                lab, actor = WorkflowLab(config['lab']), FixtureActor(**principal['actor'])
            else:
                from .workflow_pg import PgWorkflowLab
                lab = PgWorkflowLab(principal['service'], config['checkpoint_service'])
                actor = lab.store.actor
            request.state.audit_actor = {'tenant_id': actor.tenant_id, 'user_id': actor.user_id}
            return lab, actor
        except Exception:
            raise HTTPException(503, 'backend_unavailable') from None

    def perform(operation):
        try:
            return operation()
        except PermissionError:
            raise HTTPException(403, 'not_permitted') from None
        except WorkflowBusy:
            raise HTTPException(409, 'workflow_busy', headers={'Retry-After': '1'}) from None
        except (ValueError, TypeError):
            raise HTTPException(409, 'invalid_input_or_state') from None
        except Exception:
            raise HTTPException(503, 'operation_failed_inspect_before_retry') from None

    @app.get('/health/live')
    def live():
        return {'status': 'alive'}

    @app.get('/health/ready')
    def ready(context=Depends(session)):
        lab, actor = context
        def check():
            with lab._locked():
                if config['mode'] == 'postgres':
                    from .workflow_pg import connection, check_checkpoint_identity, lab_identity
                    with connection(config['checkpoint_service'], checkpoint=True) as db:
                        check_checkpoint_identity(db)
                        if lab_identity(db) != lab.store.lab_id:
                            raise ValueError('checkpoint mismatch')
                        db.execute('SELECT 1 FROM workflow_cp.checkpoints LIMIT 1')
            return {'status': 'ready', 'mode': config['mode']}
        return perform(check)

    @app.get('/v1/metrics')
    def metrics(context=Depends(session)):
        if context[1].role != 'manager':
            raise HTTPException(403, 'not_permitted')
        with counter_lock:
            return {**counters, 'scope': 'process_only', 'business_audit': 'M11 ledger'}

    @app.post('/v1/threads')
    def start(body: StartRequest, context=Depends(session)):
        lab, actor = context
        return perform(lambda: lab.start(actor, body.thread_id, body.idempotency_key, body.case,
                                         _arguments(body.case, body.arguments)))

    @app.get('/v1/threads/{thread_id}')
    def inspect(thread_id: Identifier, context=Depends(session)):
        return perform(lambda: context[0].inspect(context[1], thread_id))

    @app.post('/v1/threads/{thread_id}/decision')
    def decide(thread_id: Identifier, body: DecisionRequest, context=Depends(session)):
        return perform(lambda: context[0].decide(context[1], thread_id, body.approved))

    @app.post('/v1/threads/{thread_id}/retry')
    def retry(thread_id: Identifier, context=Depends(session)):
        return perform(lambda: context[0].retry(context[1], thread_id))

    return app


def app_from_env():
    """Uvicorn --factory entry; config is immutable until a deliberate restart."""
    return create_app(load_config(os.environ['M12_CONFIG']))
