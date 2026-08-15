"""Opaque cursor encoding (ADR-4.4: cursor/keyset pagination only, offset
banned). One tiny helper reused by every cursor-paginated list route —
threads today, more as later sprints add list endpoints.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from uuid import UUID

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


def encode_created_at_cursor(created_at: datetime, id_: UUID) -> str:
    payload = json.dumps({"created_at": created_at.isoformat(), "id": str(id_)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_created_at_cursor(cursor: str) -> tuple[datetime, UUID]:
    payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    created_at = datetime.fromisoformat(payload["created_at"])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at, UUID(payload["id"])


def encode_seq_cursor(seq: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"seq": seq}).encode()).decode()


def decode_seq_cursor(cursor: str) -> int:
    payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    return int(payload["seq"])


def clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_LIMIT))
