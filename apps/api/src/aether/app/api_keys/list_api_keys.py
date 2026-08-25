from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import ApiKey
from aether.ports.repositories import ApiKeyRepositoryPort


@dataclass(frozen=True, slots=True)
class ListApiKeysCommand:
    workspace_id: UUID


class ListApiKeys:
    def __init__(self, *, api_keys: ApiKeyRepositoryPort) -> None:
        self._api_keys = api_keys

    async def execute(self, command: ListApiKeysCommand) -> list[ApiKey]:
        return await self._api_keys.list_by_workspace(command.workspace_id)
