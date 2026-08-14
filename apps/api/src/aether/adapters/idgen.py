"""UUIDv7 generator adapter (Blueprint §3.3 IdPort, ADR-4.3: UUIDv7 for all
client-visible IDs — time-ordered, index-friendly)."""

from __future__ import annotations

from uuid import UUID

import uuid_utils


class Uuid7Generator:
    def new_id(self) -> UUID:
        # uuid_utils.uuid7() returns its own UUID type; convert to the
        # stdlib type so callers/domain entities see a plain uuid.UUID.
        return UUID(bytes=uuid_utils.uuid7().bytes)
