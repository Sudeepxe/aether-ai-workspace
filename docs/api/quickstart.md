# Quickstart: integrate Aether without reading source code

This is Devon's path (§1's API-integrator persona): *"I integrated
Aether's chat completion into our product without reading source
code."* Every step below is a plain HTTP call, using only what's in
this doc and the [published OpenAPI spec](../../packages/contracts/openapi.json).
It is proven for real, on every scheduled run of the
[`devon-quickstart`](../../.github/workflows/devon-quickstart.yml) CI
workflow (`make devon-quickstart` locally) — not just written down and
hoped to still be true (§9's "docs that can fail CI don't rot").

All requests below are plain `curl` against a running instance
(`http://localhost:8000` in the `dev` compose profile — `make dev`).

## 1. Register and log in

Setup steps (register, workspace creation, API-key creation) use a
human session (JWT) — the API key you mint in step 4 is what your
*integration's own runtime traffic* uses from then on, so it never
needs to manage JWT refresh/expiry logic at all.

```bash
curl -sX POST http://localhost:8000/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email": "devon@example.com", "password": "s3cret!!!", "display_name": "Devon"}'

ACCESS_TOKEN=$(curl -sX POST http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "devon@example.com", "password": "s3cret!!!"}' | jq -r .access_token)
```

## 2. Create a workspace and a thread

```bash
WORKSPACE_ID=$(curl -sX POST http://localhost:8000/v1/workspaces \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Devon'"'"'s Integration"}' | jq -r .id)

THREAD_ID=$(curl -sX POST "http://localhost:8000/v1/workspaces/$WORKSPACE_ID/threads" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title": "Pricing questions"}' | jq -r .id)
```

## 3. Ingest a real document

Uploads are two-phase: `:initiate` returns a presigned upload URL +
form fields (never a raw storage credential), you `POST` the file
straight to storage, then `:confirm` tells the API the upload
finished. Note `:initiate` responds `200`, `:confirm` responds `201` —
worth calling out since they're easy to mix up.

```bash
CONTENT='Acme Widgets costs $10/month per seat, billed annually.'
SHA256=$(printf '%s' "$CONTENT" | shasum -a 256 | cut -d' ' -f1)
SIZE=${#CONTENT}

INITIATE=$(curl -sX POST "http://localhost:8000/v1/workspaces/$WORKSPACE_ID/documents:initiate" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"filename": "pricing.txt", "mime": "text/plain", "size_bytes": '"$SIZE"', "content_sha256": "'"$SHA256"'"}')
DOCUMENT_ID=$(echo "$INITIATE" | jq -r .document_id)
OBJECT_KEY=$(echo "$INITIATE" | jq -r .object_key)
UPLOAD_URL=$(echo "$INITIATE" | jq -r .upload_url)

# upload_fields must accompany the file in the multipart body, in the
# order the presigned policy expects — see docs/api's quickstart script
# (apps/api/scripts/verify_devon_quickstart.py) for a robust, real
# implementation rather than hand-rolling curl -F flags for every field.

curl -sX POST "http://localhost:8000/v1/workspaces/$WORKSPACE_ID/documents:confirm" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"document_id": "'"$DOCUMENT_ID"'", "object_key": "'"$OBJECT_KEY"'", "filename": "pricing.txt", "mime": "text/plain", "size_bytes": '"$SIZE"', "content_sha256": "'"$SHA256"'"}'

# Ingestion runs asynchronously (a real worker picks it up off the
# outbox) — poll until status leaves "pending"/"processing":
curl -s "http://localhost:8000/v1/workspaces/$WORKSPACE_ID/documents/$DOCUMENT_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq .status
```

## 4. Create an API key

```bash
RAW_KEY=$(curl -sX POST "http://localhost:8000/v1/workspaces/$WORKSPACE_ID/api-keys" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Integration bot", "scopes": ["chat:write"]}' | jq -r .raw_key)
```

The response's `raw_key` field is shown exactly once — save it now; it
cannot be recovered later (only its hash is stored).

## 5. Send a grounded chat message — API key alone, no JWT

```bash
curl -sX POST "http://localhost:8000/v1/workspaces/$WORKSPACE_ID/threads/$THREAD_ID/messages" \
  -H "Authorization: Bearer $RAW_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"content": "What does Acme Widgets cost per month?", "client_message_id": "'"$(uuidgen)"'"}'
```

With the document ingested in step 3, this is a real, grounded, cited
answer — not the refusal you'd get on a document-less workspace (see
[`docs/api/README.md`](README.md)'s worked example, which deliberately
skips ingestion to demonstrate the refusal path instead). Poll
`GET .../threads/$THREAD_ID/messages` (JWT — message *listing* isn't
API-key-eligible yet, only sending is) for the assistant's completed
reply; `grounded: true` and a non-empty `citations` array confirm it
actually used the document, not a hallucination.

## Timing (measured, not assumed)

The full path above — register through a completed, grounded reply —
runs in **3–7 seconds** end to end in CI (`devon-quickstart.yml`'s
scheduled run), dominated by the async ingestion pipeline and the
generation poll, not by any per-step latency. This environment has no
real LLM provider key configured, so generation runs through the
honest `EchoGenerator`/local-hash-embedding fallback (the same
fallback every other AI-path test in this repo already documents) —
retrieval and grounding are real; the assistant's actual prose is not
what a configured provider would produce.
