from __future__ import annotations

import httpx
import pytest

from aether.adapters.email.resend import EmailSendError, ResendEmailAdapter
from aether.ports.email import EmailMessage

pytestmark = pytest.mark.unit


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_send_posts_the_expected_payload_and_auth_header() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["body"] = httpx.Request.read(request) and request.content
        return httpx.Response(200, json={"id": "abc"})

    adapter = ResendEmailAdapter(
        api_key="test-key",
        sender="noreply@aether.local",
        client=_client(httpx.MockTransport(handler)),
    )

    await adapter.send(EmailMessage(to="a@example.com", subject="Hi", text_body="hello"))

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer test-key"
    assert b'"to":["a@example.com"]' in captured["body"]  # type: ignore[operator]


async def test_send_raises_on_non_2xx_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="invalid recipient")

    adapter = ResendEmailAdapter(
        api_key="test-key",
        sender="noreply@aether.local",
        client=_client(httpx.MockTransport(handler)),
    )

    with pytest.raises(EmailSendError):
        await adapter.send(EmailMessage(to="bad", subject="Hi", text_body="hello"))
