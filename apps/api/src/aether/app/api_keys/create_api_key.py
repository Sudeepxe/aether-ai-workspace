"""CreateApiKey use case (FR-API-2, §7.4). Role gating (Admin+) happens
at the HTTP layer via domain.policy before this executes, matching every
other workspace-admin mutation's split of concerns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aether.app.auth.api_keys import generate_api_key
from aether.domain.entities import ApiKey, ApiKeyScope
from aether.ports.audit import AuditLogPort
from aether.ports.repositories import ApiKeyRepositoryPort
from aether.ports.security import IdPort


@dataclass(frozen=True, slots=True)
class CreateApiKeyCommand:
    workspace_id: UUID
    name: str
    scopes: frozenset[ApiKeyScope]
    created_by: UUID
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class CreateApiKeyResult:
    api_key: ApiKey
    raw_key: str
    """Shown to the caller exactly once — the response for this call is
    the only place this value ever appears; it cannot be recovered later
    (only ``secret_hash`` is persisted)."""


class CreateApiKey:
    def __init__(
        self,
        *,
        api_keys: ApiKeyRepositoryPort,
        audit_log: AuditLogPort,
        ids: IdPort,
        env: str,
    ) -> None:
        self._api_keys = api_keys
        self._audit_log = audit_log
        self._ids = ids
        self._env = env

    async def execute(self, command: CreateApiKeyCommand) -> CreateApiKeyResult:
        generated = generate_api_key(env=self._env)
        api_key = await self._api_keys.create(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            prefix=generated.prefix,
            secret_hash=generated.secret_hash,
            name=command.name,
            scopes=command.scopes,
            created_by=command.created_by,
            expires_at=command.expires_at,
        )
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.created_by,
            actor_key_id=None,
            action="api_key.created",
            target_type="api_key",
            target_id=api_key.id,
            metadata={"name": command.name, "scopes": [s.value for s in command.scopes]},
        )
        return CreateApiKeyResult(api_key=api_key, raw_key=generated.raw_key)
