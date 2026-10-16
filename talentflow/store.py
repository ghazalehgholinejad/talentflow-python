"""SQLite persistence: one atomic action, including its idempotency receipt."""
from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path

from .domain import DomainError, PUBLIC, READS, execute, text


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Store:
    def __init__(self, path):
        self.path = str(path)
        if self.path == ':memory:':
            raise ValueError('Use a file path; each request owns its connection')
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS records (
                    key TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL
                );
            ''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    def run(self, payload, now=None):
        payload = {k: v for k, v in payload.items() if k != 'sessionToken'}
        action = payload.get('action')
        if not isinstance(action, str):
            raise DomainError('action must be a string')
        request_id = payload.get('requestId')
        if action in ['createJob', 'importCandidate'] and not request_id:
            raise DomainError('requestId is required for record creation')
        if request_id is not None:
            request_id = text(request_id, 'requestId', 100)
        # Candidate operations always recheck token and expiry; never replay a token-auth response.
        cache = request_id is not None and action not in READS | PUBLIC
        fingerprint = hashlib.sha256(encode({k: v for k, v in payload.items() if k != 'requestId'}).encode()).hexdigest()
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            if cache:
                receipt = db.execute('SELECT fingerprint,response FROM requests WHERE request_id=?', (request_id,)).fetchone()
                if receipt:
                    if receipt[0] != fingerprint:
                        raise DomainError('requestId was already used for a different payload', 409)
                    db.commit()
                    return json.loads(receipt[1])
            state = {key: {'kind': kind, 'data': json.loads(value)}
                     for key, kind, value in db.execute('SELECT key,kind,payload FROM records ORDER BY rowid')}
            result, updated = execute(state, payload, now=now)
            for key, row in updated.items():
                if row != state.get(key):
                    db.execute('INSERT INTO records VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',
                               (key, row['kind'], encode(row['data'])))
            response = {'ok': True, 'version': 'talentflow-v2', 'backend': 'python-sqlite', 'mock': True, 'result': result}
            if cache:
                db.execute('INSERT INTO requests VALUES (?,?,?)', (request_id, fingerprint, encode(response)))
            db.commit()
            return response
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
