"""Proves docs/api/quickstart.md's literal claim for real (S10 #110,
§11.6's exit criterion: "Devon-persona quickstart works cold") — every
step below is a plain HTTP call against a real running instance, using
only what's in the published OpenAPI spec and that doc, exactly as an
external integrator with no access to this source tree would do it.

Not a pytest file: this is meant to be run as a standalone script
against a real booted stack (`devon-quickstart.yml`'s CI job, or
locally via `make devon-quickstart`), timing itself honestly rather
than asserting silently — §9's own stated philosophy is that an
unverified claim is a wish, and a claim nobody times is just as
unverified as one nobody runs.
"""

from __future__ import annotations

import hashlib
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

_BASE_URL = "http://localhost:8000"
_TIMEOUT = 30.0


@dataclass
class StepTimer:
    label: str
    start: float

    def done(self) -> float:
        elapsed = time.perf_counter() - self.start
        print(f"  [{elapsed:6.2f}s] {self.label}")
        return elapsed


def _step(label: str) -> StepTimer:
    print(f"-> {label}")
    return StepTimer(label=label, start=time.perf_counter())


def _check(resp: httpx.Response, *, expect: int) -> httpx.Response:
    if resp.status_code != expect:
        print(f"FAIL: expected {expect}, got {resp.status_code}: {resp.text}", file=sys.stderr)
        raise SystemExit(1)
    return resp


def main() -> int:
    total_start = time.perf_counter()
    with httpx.Client(base_url=_BASE_URL, timeout=_TIMEOUT) as client:
        email = f"devon-{uuid.uuid4().hex[:8]}@example.com"
        password = "s3cret!!!"  # noqa: S105 — a fresh throwaway registration's own password, not a credential

        t = _step("register")
        _check(
            client.post(
                "/v1/auth/register",
                json={"email": email, "password": password, "display_name": "Devon"},
            ),
            expect=201,
        )
        t.done()

        t = _step("log in")
        login_resp = _check(
            client.post("/v1/auth/login", json={"email": email, "password": password}),
            expect=200,
        )
        access_token = login_resp.json()["access_token"]
        auth_headers = {"Authorization": f"Bearer {access_token}"}
        t.done()

        t = _step("create a workspace")
        ws_resp = _check(
            client.post(
                "/v1/workspaces", json={"name": "Devon's Integration"}, headers=auth_headers
            ),
            expect=201,
        )
        workspace_id = ws_resp.json()["id"]
        t.done()

        t = _step("create a thread")
        thread_resp = _check(
            client.post(
                f"/v1/workspaces/{workspace_id}/threads",
                json={"title": "Pricing questions"},
                headers=auth_headers,
            ),
            expect=201,
        )
        thread_id = thread_resp.json()["id"]
        t.done()

        t = _step("ingest a real document (initiate + upload + confirm)")
        content = b"Acme Widgets costs $10/month per seat, billed annually."
        content_sha256 = hashlib.sha256(content).hexdigest()
        initiate_resp = _check(
            client.post(
                f"/v1/workspaces/{workspace_id}/documents:initiate",
                json={
                    "filename": "pricing.txt",
                    "mime": "text/plain",
                    "size_bytes": len(content),
                    "content_sha256": content_sha256,
                },
                headers=auth_headers,
            ),
            expect=200,
        )
        initiate = initiate_resp.json()
        document_id, object_key = initiate["document_id"], initiate["object_key"]

        upload_resp = httpx.post(
            initiate["upload_url"],
            data=initiate["upload_fields"],
            files={"file": ("pricing.txt", content, "text/plain")},
            timeout=_TIMEOUT,
        )
        if upload_resp.status_code not in (200, 204):
            print(f"FAIL: presigned upload returned {upload_resp.status_code}", file=sys.stderr)
            return 1

        _check(
            client.post(
                f"/v1/workspaces/{workspace_id}/documents:confirm",
                json={
                    "document_id": document_id,
                    "object_key": object_key,
                    "filename": "pricing.txt",
                    "mime": "text/plain",
                    "size_bytes": len(content),
                    "content_sha256": content_sha256,
                },
                headers=auth_headers,
            ),
            expect=201,
        )
        t.done()

        t = _step("wait for ingestion to reach 'ready' (real worker pipeline, async)")
        deadline = time.perf_counter() + 60.0
        status = None
        while time.perf_counter() < deadline:
            doc = _check(
                client.get(
                    f"/v1/workspaces/{workspace_id}/documents/{document_id}",
                    headers=auth_headers,
                ),
                expect=200,
            ).json()
            status = doc["status"]
            if status in ("ready", "failed"):
                break
            time.sleep(1)
        if status != "ready":
            print(f"FAIL: document never reached 'ready' (last status: {status})", file=sys.stderr)
            return 1
        t.done()

        t = _step("create an API key, scoped to chat:write")
        key_resp = _check(
            client.post(
                f"/v1/workspaces/{workspace_id}/api-keys",
                json={"name": "Quickstart bot", "scopes": ["chat:write"]},
                headers=auth_headers,
            ),
            expect=201,
        )
        raw_key = key_resp.json()["raw_key"]
        t.done()

        t = _step("send a grounded chat message — API key ALONE, no JWT")
        api_key_headers = {"Authorization": f"Bearer {raw_key}"}
        message_resp = _check(
            client.post(
                f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
                json={
                    "content": "What does Acme Widgets cost per month?",
                    "client_message_id": uuid.uuid4().hex,
                },
                headers=api_key_headers,
            ),
            expect=202,
        )
        generation_id = message_resp.json()["generation_id"]
        t.done()

        t = _step("poll the generation until it settles, verify it's grounded")
        # Message *listing* isn't API-key-eligible yet (only the send route
        # is, per #105/#107's scoping — see docs/api/README.md's note),
        # so this one poll uses the JWT session that's still in scope from
        # setup, not the API key.
        deadline = time.perf_counter() + 30.0
        message = None
        while time.perf_counter() < deadline:
            messages = _check(
                client.get(
                    f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
                    headers=auth_headers,
                ),
                expect=200,
            ).json()["items"]
            assistant_messages = [m for m in messages if m["role"] == "assistant"]
            if assistant_messages and assistant_messages[-1]["status"] == "complete":
                message = assistant_messages[-1]
                break
            time.sleep(1)
        if message is None:
            print("FAIL: assistant message never completed", file=sys.stderr)
            return 1
        if not message["grounded"]:
            print(
                f"FAIL: expected a grounded answer citing the ingested document, "
                f"got an ungrounded refusal instead: {message['content'][:200]!r}",
                file=sys.stderr,
            )
            return 1
        t.done()

    total_elapsed = time.perf_counter() - total_start
    print(f"\nPASS: Devon-persona quickstart works cold — {total_elapsed:.1f}s total")
    print(f"generation_id={generation_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
