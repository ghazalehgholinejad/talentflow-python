# API contract

Use `POST` with `Content-Type: application/json`. Action-specific arguments are top-level fields alongside `action`. The Pydantic envelope checks action names and request-ID shape; domain functions validate the fields used by each action. Unused extra fields are tolerated for compatibility and confer no permissions. `sessionToken` is ignored: tokens are generated in the backend.

Success:

```json
{"ok":true,"version":"talentflow-v2","backend":"python-sqlite","mock":true,"result":{}}
```

Error:

```json
{"ok":false,"error":"A descriptive message"}
```

HTTP 401 means missing/wrong recruiter credential; 403 means denied origin, candidate scope or token; 404 is a missing record; 409 is a conflicting request ID, duplicate email or illegal state transition; 413 is body size; 415 is media type; 422 is invalid input; 503 is an operational SQLite failure. Framework-level errors (for example invalid Host or unknown route) may use framework response formats.

## Recruiter endpoint

`/api/talentflow` requires header `x-talentflow-secret`. It can call any listed action. Candidate operations still validate their interview token, even on this endpoint.

| Action | Fields | Result |
| --- | --- | --- |
| snapshot | none | jobs, candidates, applications |
| createJob | requestId, title, department, location, goal, skills; optional minExperience, passScore | draft job |
| updateJob | jobId, status active/closed; confirmed=true for activation | updated job |
| syncResumes | none | count and newly added fictional candidates |
| importCandidate | requestId, name, email, skills, experience, resumeText | unverified candidate |
| verifyCandidate | candidateId, confirmed=true | verified candidate |
| runScreening | jobId, integer limit 1–40, minScore 0–100 | new applications and count |
| updateApplication | applicationId, status, note | updated application |
| createInterview | approved applicationId | application with session |
| recruiterReport | optional applicationId | report for one or all applications |

`skills` accepts a string list or comma/Persian-comma/newline separated text. Numeric form strings remain supported for the frontend. Strings are trimmed, email lowercased. Empty skills, booleans as numbers, non-finite numbers, and fractional shortlist limits are rejected.

Allowed human-decision transitions:

```text
suggested → approved → interviewed → hired
          ↘ rejected              ↘ rejected_after_interview
```

`approved → interviewed` happens only when all answers are submitted and the candidate finishes. A nonempty human note is required for a new recruiter decision. Repeating an already-applied decision returns current state; it does not add another history event. `createInterview` does not rotate an existing token.

## Candidate endpoint

`/api/candidate` accepts only:

| Action | Fields |
| --- | --- |
| getInterview | token |
| questionAudio | token, questionId |
| saveAnswer | token, questionId, text; optional source |
| finishInterview | token |

The token is checked on every call, including repeated completion. Public interview responses omit the token, email, invitation and recruiter history. `questionAudio` returns text for browser speech, not generated audio. Answers use `typed` unless explicitly marked `mock-stt`.

## Durable request IDs

Creation requires a nonempty string `requestId` (at most 100 characters). Other recruiter mutations may also supply one. Use a new random ID for a new logical operation, and the same ID **and same payload** only when retrying that operation.

The first successful keyed mutation stores a canonical payload fingerprint and response in the same transaction as its writes. A repeated key with the same payload returns the original response, which can be older than current state; use `snapshot` for current state. A different payload with that key returns 409. JSON object key order is ignored; list order, numeric strings versus numbers, and extra fields remain significant. Keys are global to this single-recruiter database, not per action.

Failed mutations are rolled back and do not reserve a key. Read operations do not create receipts. Candidate operations do not use receipts: answer replacement and repeated completion follow their domain rules, and expiry is rechecked. The existing frontend generates request IDs; this backend cannot deduplicate two creation requests with different IDs.

## Request example

```json
{
  "action":"createJob",
  "requestId":"my-first-job-2026-10-16",
  "title":"Python Engineer",
  "department":"Demo",
  "location":"Remote",
  "goal":"Build a local API",
  "skills":["Python","SQL"],
  "minExperience":2
}
```
