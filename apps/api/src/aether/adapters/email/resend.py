"""Resend EmailPort adapter (ADR-11.1). Provider swap for prod profiles
that want a managed transactional-email API instead of operating SMTP.
"""

from __future__ import annotations

import httpx

from aether.ports.email import EmailMessage

_RESEND_API_URL = "https://api.resend.com/emails"


class EmailSendError(RuntimeError):
    """Resend returned a non-2xx response — the worker's retry policy
    (§3.6.2) is what decides whether to try again, not this adapter."""


class ResendEmailAdapter:
    def __init__(
        self, *, api_key: str, sender: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._api_key = api_key
        self._sender = sender
        # Accepts an injected client so tests never make a real network
        # call — see tests/unit/test_resend_adapter.py.
        self._client = client or httpx.AsyncClient(timeout=5.0)

    async def send(self, message: EmailMessage) -> None:
        response = await self._client.post(
            _RESEND_API_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "from": self._sender,
                "to": [message.to],
                "subject": message.subject,
                "text": message.text_body,
            },
        )
        if response.status_code >= 400:
            raise EmailSendError(f"Resend returned {response.status_code}: {response.text}")
