"""DeleteDocument use case (§4.3: DELETE .../documents/{document},
FR-KB-5's provable deletion). Synchronous: the actual content (chunks +
vectors) is gone by the time this call returns — see
DocumentRepositoryPort.delete's docstring for why this isn't the
async worker-saga DF-3 describes (app_api's grants make it the sole
owner of the hard-delete step, so there's no cross-process handoff to
make async).

Raises DocumentNotFoundError (not a silent no-op) if nothing matched:
unlike DeleteThread's idempotent-no-op posture, a document delete that
silently no-ops on an already-deleted or nonexistent id would let a
caller believe content was removed when it was actually never there to
remove — FR-KB-5's "provable" framing argues for the stricter behavior
here specifically.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.errors import DocumentNotFoundError
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.repositories import DocumentRepositoryPort
from aether.ports.security import ClockPort, IdPort

DOCUMENT_DELETED_EVENT_TYPE = "document.deleted"


@dataclass(frozen=True, slots=True)
class DeleteDocumentCommand:
    workspace_id: UUID
    document_id: UUID


class DeleteDocument:
    def __init__(
        self,
        *,
        documents: DocumentRepositoryPort,
        outbox: OutboxRepositoryPort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._documents = documents
        self._outbox = outbox
        self._clock = clock
        self._ids = ids

    async def execute(self, command: DeleteDocumentCommand) -> None:
        deleted = await self._documents.delete(
            command.workspace_id, command.document_id, deleted_at=self._clock.now()
        )
        if not deleted:
            raise DocumentNotFoundError(str(command.document_id))
        await self._outbox.enqueue(
            id=self._ids.new_id(),
            aggregate_type="document",
            aggregate_id=command.document_id,
            event_type=DOCUMENT_DELETED_EVENT_TYPE,
            tenant_id=command.workspace_id,
            payload={"document_id": str(command.document_id)},
        )
