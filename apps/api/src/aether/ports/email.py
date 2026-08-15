"""EmailPort (ADR-11.1 gap remediation) — transactional email in the
hexagon, SMTP and Resend adapters implementing it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text_body: str


class EmailPort(Protocol):
    async def send(self, message: EmailMessage) -> None: ...
