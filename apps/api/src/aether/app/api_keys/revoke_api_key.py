"""RevokeApiKey use case (§4.3: DELETE /workspaces/{ws}/api-keys/{id}).
Role gating (Admin+) happens at the HTTP layer.

A soft revoke (``revoked_at`` set), not a row delete — unlike
invitations (ephemeral, single-use tokens with no ongoing audit value),
a revoked key's row is worth keeping: it's what makes
``audit_events.actor_key_id`` still resolvable, and what lets an admin
see "this key existed and was revoked on <date>" rather than the key
simply vanishing from history."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.ports.audit import AuditLogPort
from aether.ports.repositories import ApiKeyRepositoryPort
from aether.ports.security import ClockPort, IdPort


@dataclass(frozen=True, slots=True)
class RevokeApiKeyCommand:
    workspace_id: UUID
    key_id: UUID
    actor_user_id: UUID


class RevokeApiKey:
    def __init__(
        self,
        *,
        api_keys: ApiKeyRepositoryPort,
        audit_log: AuditLogPort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._api_keys = api_keys
        self._audit_log = audit_log
        self._clock = clock
        self._ids = ids

    async def execute(self, command: RevokeApiKeyCommand) -> None:
        await self._api_keys.revoke(
            command.workspace_id, command.key_id, revoked_at=self._clock.now()
        )
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            actor_key_id=None,
            action="api_key.revoked",
            target_type="api_key",
            target_id=command.key_id,
            metadata={},
        )
