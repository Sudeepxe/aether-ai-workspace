from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aether.domain.entities import PasswordResetToken


class FakePasswordResetTokenRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, PasswordResetToken] = {}
        self.created: list[PasswordResetToken] = []

    async def create(
        self, *, id: UUID, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> PasswordResetToken:
        token = PasswordResetToken(
            id=id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            consumed_at=None,
            created_at=datetime.now().astimezone(),
        )
        self._rows[id] = token
        self.created.append(token)
        return token

    async def get_by_token_hash(self, token_hash: str) -> PasswordResetToken | None:
        for token in self._rows.values():
            if token.token_hash == token_hash:
                return token
        return None

    async def consume(self, token_id: UUID, *, consumed_at: datetime) -> None:
        current = self._rows[token_id]
        self._rows[token_id] = PasswordResetToken(
            id=current.id,
            user_id=current.user_id,
            token_hash=current.token_hash,
            expires_at=current.expires_at,
            consumed_at=consumed_at,
            created_at=current.created_at,
        )
