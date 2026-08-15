"""SMTP EmailPort adapter (ADR-11.1). Talks to mailpit in the local dev
profile; any standard SMTP relay in other profiles.

smtplib is blocking stdlib I/O — run via asyncio.to_thread() so a slow
mail relay can't stall the event loop (Ch.4 "Common Junior-Engineer
Mistake #1"), rather than pulling in an async SMTP library for what's a
low-volume, non-hot-path send.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage as MimeEmailMessage

from aether.ports.email import EmailMessage


class SmtpEmailAdapter:
    def __init__(self, *, host: str, port: int, sender: str) -> None:
        self._host = host
        self._port = port
        self._sender = sender

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        mime_message = MimeEmailMessage()
        mime_message["From"] = self._sender
        mime_message["To"] = message.to
        mime_message["Subject"] = message.subject
        mime_message.set_content(message.text_body)

        with smtplib.SMTP(self._host, self._port, timeout=5) as smtp:
            smtp.send_message(mime_message)
