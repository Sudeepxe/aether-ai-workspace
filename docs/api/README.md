# API documentation

The published API contract lives at [`packages/contracts/openapi.json`](../../packages/contracts/openapi.json)
— a **generated artifact** (ADR-9.2), regenerated with `make openapi` and
diffed for drift in CI (`make openapi-check`, the `contract` job). It is
never hand-edited. The same spec is served live at `GET /openapi.json`
against any running instance; the interactive Swagger UI (`/docs`) is
enabled in the `dev` environment only (OWASP API8 — the spec itself
isn't sensitive, the *interactive* "try it out" surface is what's
worth gating).

## Authentication

Two credential types share the same `Authorization: Bearer <token>`
header; the API tells them apart by prefix (a JWT never starts with
`aeth_`).

- **Session (JWT)** — issued by `POST /v1/auth/login`, short-lived
  (15 min default), paired with a refresh token for the web app. Used
  for anything a human does interactively.
- **API key** — `aeth_{env}_{prefix}{secret}` (§7.4), workspace-scoped,
  carries explicit scopes (`chat:write`, `kb:read`), created via
  `POST /workspaces/{workspace_id}/api-keys` by a workspace Admin or
  Owner. This is what an external integration (Devon's persona) uses —
  it never needs a human session token. The raw key is returned exactly
  once, at creation; only its SHA-256 hash is stored.

As of S10, API keys authenticate the chat message-send route
(`POST /workspaces/{workspace_id}/threads/{thread_id}/messages`) —
the route an external integration actually needs. Broader route
coverage (thread/document management via API key) is tracked
separately; see the repo's open issues for current status rather than
assuming this document is exhaustive.

## Worked example: create a workspace, mint an API key, send a grounded chat turn

All requests below are plain `curl` against a running instance
(`http://localhost:8000` in the `dev` compose profile — `make dev`).

**1. Register and log in** (JWT — needed once, to create the workspace and key):

```bash
curl -sX POST http://localhost:8000/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email": "devon@example.com", "password": "s3cret!!!", "display_name": "Devon"}'

ACCESS_TOKEN=$(curl -sX POST http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "devon@example.com", "password": "s3cret!!!"}' | jq -r .access_token)
```

**2. Create a workspace:**

```bash
WORKSPACE_ID=$(curl -sX POST http://localhost:8000/v1/workspaces \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Devon'"'"'s Integration"}' | jq -r .id)
```

**3. Create an API key, scoped to `chat:write`:**

```bash
RAW_KEY=$(curl -sX POST "http://localhost:8000/v1/workspaces/$WORKSPACE_ID/api-keys" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Integration bot", "scopes": ["chat:write"]}' | jq -r .raw_key)
```

The response's `raw_key` field is shown exactly once — save it now; it
cannot be recovered later (only its hash is stored).

**4. Create a thread (still JWT — thread management isn't API-key-eligible yet, see the note above), then send a message using the API key alone:**

```bash
THREAD_ID=$(curl -sX POST "http://localhost:8000/v1/workspaces/$WORKSPACE_ID/threads" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title": "First integration thread"}' | jq -r .id)

curl -sX POST "http://localhost:8000/v1/workspaces/$WORKSPACE_ID/threads/$THREAD_ID/messages" \
  -H "Authorization: Bearer $RAW_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"content": "What does our pricing page say?", "client_message_id": "'"$(uuidgen)"'"}'
```

Without documents ingested into the workspace, retrieval finds nothing
and the assistant refuses gracefully (ADR-6.4) rather than
hallucinating — the same grammar and idempotency machinery a grounded,
cited answer uses, just with different content. A full document-
ingestion-through-API-key walkthrough, plus a script that proves this
exact path works end to end on a clean checkout, lands with the
Devon-persona quickstart (tracked separately).

## Idempotent retries

Plain mutating POSTs (workspace creation, invitation creation, API-key
creation) accept an `Idempotency-Key` header (ADR-4.6): a retried
request with the same key and the same body replays the original
response (`Idempotent-Replay: true`) instead of creating a duplicate;
the same key with a different body is a `409`. Chat message sends use
their own `client_message_id` field for the same purpose (shown above)
— pass a stable UUID per logical turn and a network retry is always
safe.
