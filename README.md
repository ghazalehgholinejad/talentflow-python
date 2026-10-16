# TalentFlow Python

A **FastAPI + SQLite backend** for a local recruitment-workflow learning project. It implements the TalentFlow action contract without an n8n account: jobs, reviewed resumes, shortlisting, interview sessions, answers, reports, and explicit human decisions.

The practical lesson is **transactional application design and idempotency**: a retried request must not silently create a second record. This is a Python port of my TalentFlow workflow, with a server-generated interview token and a durable request receipt committed alongside the records.

**All scoring, question generation and email delivery are deterministic mocks.** This is not an AI hiring assessment or a production recruitment system. No real email is sent. Use fictional data.

[درس فارسی و تمرین‌ها](docs/LEARN_FA.md) · [API contract](docs/API.md) · [Architecture](docs/ARCHITECTURE.md)

## Run locally

Python 3.11+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
export TALENTFLOW_API_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export TALENTFLOW_DB=./data/talentflow.sqlite3
uvicorn talentflow.api:create_app --factory --host 127.0.0.1 --port 8000
```

The generated secret lives in this shell's environment. Configure the same value in the frontend's ignored `.env.local` file, or retain it securely for another terminal. Do not commit or put it in a browser/public environment variable. The application reads environment variables; it does not automatically load `.env.example`.

- `http://127.0.0.1:8000/health`: non-sensitive liveness response.
- `http://127.0.0.1:8000/docs`: action-envelope documentation; action-specific fields are listed in [API.md](docs/API.md).
- `POST /api/talentflow`: privileged recruiter actions, header `x-talentflow-secret` required.
- `POST /api/candidate`: four interview actions; the unguessable session token is required in each JSON body.

Example in a terminal with the same environment:

```bash
curl --fail-with-body http://127.0.0.1:8000/api/talentflow \
  -H 'Content-Type: application/json' \
  -H "x-talentflow-secret: $TALENTFLOW_API_KEY" \
  --data '{"action":"snapshot"}'
```

A new database starts empty. Try the fictional lifecycle without starting HTTP:

```bash
python examples/offline_demo.py
python -m pytest -q
```

The demo uses a temporary database and deletes it afterwards. It prints only aggregate workflow status, not interview tokens. The application server persists its configured SQLite file across restarts.

## Connect the existing TalentFlow dashboard

The updated TalentFlow frontend proxy supports these server-only settings in `.env.local`:

```dotenv
TALENTFLOW_BACKEND=python
TALENTFLOW_PYTHON_URL=http://127.0.0.1:8000
TALENTFLOW_API_KEY=the-same-generated-secret-as-the-python-server
```

Restart the frontend after editing its environment. The browser continues using the same-origin `/api/talentflow` and `/api/candidate` proxy. Keep both programs on loopback. Backend switching does **not** migrate n8n records; SQLite is independent. Unset the selector or use `TALENTFLOW_BACKEND=n8n` to retain the previous backend.

## What is implemented

- Fifteen action names compatible with the existing frontend response envelope.
- Human confirmation before activating jobs, verifying candidates, and approving interviews.
- Deterministic, job-specific mock shortlisting; no duplicate job/candidate pair.
- Server-generated 256-bit interview tokens with seven-day expiry.
- Replaceable answers until completion, required answers before completion, immutable completed answers.
- Transactional persistence and idempotency-key conflict detection.
- Tests for lifecycle, persistence, retries, concurrent requests, rollback, invalid input and HTTP access boundaries.

## Deliberate limits

Single-recruiter, local educational service. A shared API key is not a user-account/role system. No production rate limiting, audit export, file upload, real LLM/STT/email, data migration, encryption-at-rest, or retention/deletion workflow is provided. Interview tokens and receipt responses are stored in plaintext in the local database, so protect database files and backups. The bearer token grants access to that interview until expiry; individual token revocation is not exposed in this release.

Each request reads the small demo record collection. `BEGIN IMMEDIATE` serializes operations against one SQLite file with a ten-second connection timeout; it is not a distributed transaction. Receipts are retained indefinitely in this prototype. Do not place the database on a shared/network filesystem or expose the app publicly. The transport caps request bytes but is not a general denial-of-service defense.

The action contract is retained, but byte-for-byte parity is not claimed: IDs are server-generated, some wording differs, input validation rejects booleans as numbers, skills are capped at 30 rather than silently truncated, and changed payloads reusing a recorded request ID fail with HTTP 409. Creation requires `requestId`. See [API.md](docs/API.md) for replay rules.

## Learning sources

The code is an original Python implementation of this project's workflow. The tutorial explains its design choices. Framework references: [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/), [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/), [Python sqlite3](https://docs.python.org/3/library/sqlite3.html). Reviewed 2026-10-16. CI verifies the current resolved dependency versions; this repository does not promise compatibility with every allowed future dependency release.
