"""Local FastAPI transport. API credentials never belong in the browser."""
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .domain import DomainError, PUBLIC
from .store import Store
from .models import ActionRequest

MAX_BODY = 150_000


def _reject_constant(value):
    raise ValueError('Non-finite number')


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def create_app(db_path=None, api_key=None):
    key = api_key if api_key is not None else os.environ.get('TALENTFLOW_API_KEY', '')
    if len(key) < 32 or not key.isascii():
        raise RuntimeError('Set TALENTFLOW_API_KEY to at least 32 ASCII characters')
    path = db_path or os.environ.get('TALENTFLOW_DB', './data/talentflow.sqlite3')

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(path)
        yield

    app = FastAPI(title='TalentFlow Python', version='0.1.0', lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'])

    def response(body, status=200):
        return JSONResponse(body, status_code=status, headers={'Cache-Control': 'no-store'})

    async def dispatch(request, candidate=False):
        # Browser callers should use the existing same-origin frontend proxy.
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return response({'ok': False, 'error': 'Cross-origin requests are not allowed'}, 403)
        if not candidate:
            supplied = request.headers.get('x-talentflow-secret', '')
            if not supplied.isascii() or not secrets.compare_digest(supplied, key):
                return response({'ok': False, 'error': 'Invalid API credential'}, 401)
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            return response({'ok': False, 'error': 'Expected application/json'}, 415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                return response({'ok': False, 'error': 'Request exceeds 150000 bytes'}, 413)
        try:
            payload = json.loads(body.decode('utf-8'), parse_constant=_reject_constant, object_pairs_hook=_pairs)
            if not isinstance(payload, dict) or not isinstance(payload.get('action'), str):
                raise ValueError('Expected an object with action')
            if candidate and payload['action'] not in PUBLIC:
                return response({'ok': False, 'error': 'Recruiter action is not allowed here'}, 403)
            payload = ActionRequest.model_validate(payload).model_dump(exclude_none=True)
            result = await run_in_threadpool(app.state.store.run, payload)
            return response(result)
        except DomainError as exc:
            return response({'ok': False, 'error': str(exc)}, exc.status)
        except (ValueError, UnicodeError, RecursionError):
            return response({'ok': False, 'error': 'Invalid JSON or input value'}, 422)
        except sqlite3.OperationalError:
            return response({'ok': False, 'error': 'Storage temporarily unavailable; inspect state before retrying'}, 503)

    @app.get('/health')
    def health():
        return {'ok': True, 'backend': 'python-sqlite', 'mock': True}

    body_schema = {'requestBody': {'required': True, 'content': {'application/json': {'schema': ActionRequest.model_json_schema()}}}}

    @app.post('/api/talentflow', openapi_extra=body_schema)
    async def recruiter(request: Request):
        """Recruiter action envelope; requires x-talentflow-secret header."""
        return await dispatch(request)

    @app.post('/api/candidate', openapi_extra=body_schema)
    async def candidate(request: Request):
        """Only interview actions. A valid token in the JSON body is required."""
        return await dispatch(request, candidate=True)

    return app
