from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aether.app.documents.confirm_upload import (
    DOCUMENT_UPLOADED_EVENT_TYPE,
    ConfirmDocumentUpload,
    ConfirmDocumentUploadCommand,
)
from aether.app.documents.delete_document import (
    DOCUMENT_DELETED_EVENT_TYPE,
    DeleteDocument,
    DeleteDocumentCommand,
)
from aether.app.documents.get_document import GetDocument, GetDocumentCommand
from aether.app.documents.initiate_upload import (
    InitiateDocumentUpload,
    InitiateDocumentUploadCommand,
)
from aether.app.documents.list_documents import ListDocuments, ListDocumentsCommand
from aether.domain.entities import DocumentStatus
from aether.domain.errors import DocumentNotFoundError, DocumentUploadIncompleteError
from tests.unit.fakes.auth import FakeClock, FakeIdGenerator
from tests.unit.fakes.documents import FakeDocumentObjectStorage, FakeDocumentRepository
from tests.unit.fakes.outbox import FakeOutboxRepository

pytestmark = pytest.mark.unit

_CONTENT_HASH = "a" * 64


async def test_initiate_upload_returns_a_content_addressed_presigned_upload() -> None:
    storage = FakeDocumentObjectStorage()
    ids = FakeIdGenerator()
    workspace_id = uuid4()

    result = await InitiateDocumentUpload(object_storage=storage, ids=ids).execute(
        InitiateDocumentUploadCommand(
            workspace_id=workspace_id,
            mime="text/markdown",
            size_bytes=1024,
            content_sha256=_CONTENT_HASH,
        )
    )

    assert result.object_key == f"{workspace_id}/{_CONTENT_HASH}"
    assert result.upload_url == "https://storage.example/bucket"
    assert result.upload_fields["key"] == result.object_key


async def _confirm(
    *,
    documents: FakeDocumentRepository,
    storage: FakeDocumentObjectStorage,
    outbox: FakeOutboxRepository,
    ids: FakeIdGenerator,
    workspace_id,
    document_id,
    object_key: str,
):
    return await ConfirmDocumentUpload(
        documents=documents, object_storage=storage, outbox=outbox, ids=ids
    ).execute(
        ConfirmDocumentUploadCommand(
            workspace_id=workspace_id,
            document_id=document_id,
            filename="notes.md",
            mime="text/markdown",
            size_bytes=1024,
            content_sha256=_CONTENT_HASH,
            object_key=object_key,
        )
    )


async def test_confirm_upload_creates_the_document_and_enqueues_document_uploaded() -> None:
    workspace_id, document_id = uuid4(), uuid4()
    object_key = f"{workspace_id}/{_CONTENT_HASH}"
    storage = FakeDocumentObjectStorage(existing_keys={object_key})
    documents = FakeDocumentRepository()
    outbox = FakeOutboxRepository()
    ids = FakeIdGenerator()

    document = await _confirm(
        documents=documents,
        storage=storage,
        outbox=outbox,
        ids=ids,
        workspace_id=workspace_id,
        document_id=document_id,
        object_key=object_key,
    )

    assert document.id == document_id
    assert document.status == DocumentStatus.QUEUED
    pending = await outbox.fetch_pending(
        event_type=DOCUMENT_UPLOADED_EVENT_TYPE, max_attempts=999, limit=10
    )
    assert len(pending) == 1
    assert pending[0].payload == {
        "document_id": str(document_id),
        "object_key": object_key,
        "mime": "text/markdown",
    }
    assert pending[0].tenant_id == workspace_id


async def test_confirm_upload_raises_if_the_object_was_never_actually_uploaded() -> None:
    workspace_id, document_id = uuid4(), uuid4()
    object_key = f"{workspace_id}/{_CONTENT_HASH}"
    storage = FakeDocumentObjectStorage(existing_keys=set())  # nothing uploaded
    documents = FakeDocumentRepository()
    outbox = FakeOutboxRepository()
    ids = FakeIdGenerator()

    with pytest.raises(DocumentUploadIncompleteError):
        await _confirm(
            documents=documents,
            storage=storage,
            outbox=outbox,
            ids=ids,
            workspace_id=workspace_id,
            document_id=document_id,
            object_key=object_key,
        )
    assert await documents.list_by_workspace(workspace_id, after=None, limit=10) == []
    assert (
        await outbox.fetch_pending(
            event_type=DOCUMENT_UPLOADED_EVENT_TYPE, max_attempts=999, limit=10
        )
        == []
    )


async def test_confirm_upload_is_idempotent_under_a_retried_call() -> None:
    """A retried :confirm (network retry after the first response was
    lost) must not create a duplicate document.uploaded event."""
    workspace_id, document_id = uuid4(), uuid4()
    object_key = f"{workspace_id}/{_CONTENT_HASH}"
    storage = FakeDocumentObjectStorage(existing_keys={object_key})
    documents = FakeDocumentRepository()
    outbox = FakeOutboxRepository()
    ids = FakeIdGenerator()

    first = await _confirm(
        documents=documents,
        storage=storage,
        outbox=outbox,
        ids=ids,
        workspace_id=workspace_id,
        document_id=document_id,
        object_key=object_key,
    )
    second = await _confirm(
        documents=documents,
        storage=storage,
        outbox=outbox,
        ids=ids,
        workspace_id=workspace_id,
        document_id=document_id,
        object_key=object_key,
    )

    assert first.id == second.id
    pending = await outbox.fetch_pending(
        event_type=DOCUMENT_UPLOADED_EVENT_TYPE, max_attempts=999, limit=10
    )
    assert len(pending) == 1  # not duplicated on the retry


async def test_get_document_raises_not_found_for_unknown_id() -> None:
    with pytest.raises(DocumentNotFoundError):
        await GetDocument(documents=FakeDocumentRepository()).execute(
            GetDocumentCommand(workspace_id=uuid4(), document_id=uuid4())
        )


async def test_get_document_raises_not_found_across_workspaces() -> None:
    workspace_a, workspace_b = uuid4(), uuid4()
    document_id = uuid4()
    object_key = f"{workspace_a}/{_CONTENT_HASH}"
    documents = FakeDocumentRepository()
    storage = FakeDocumentObjectStorage(existing_keys={object_key})
    outbox = FakeOutboxRepository()
    ids = FakeIdGenerator()
    await _confirm(
        documents=documents,
        storage=storage,
        outbox=outbox,
        ids=ids,
        workspace_id=workspace_a,
        document_id=document_id,
        object_key=object_key,
    )

    with pytest.raises(DocumentNotFoundError):
        await GetDocument(documents=documents).execute(
            GetDocumentCommand(workspace_id=workspace_b, document_id=document_id)
        )


async def test_list_documents_orders_newest_first_and_paginates() -> None:
    documents = FakeDocumentRepository()
    outbox = FakeOutboxRepository()
    ids = FakeIdGenerator()
    workspace_id = uuid4()
    for i in range(5):
        content_hash = str(i) * 64
        object_key = f"{workspace_id}/{content_hash}"
        storage = FakeDocumentObjectStorage(existing_keys={object_key})
        await ConfirmDocumentUpload(
            documents=documents, object_storage=storage, outbox=outbox, ids=ids
        ).execute(
            ConfirmDocumentUploadCommand(
                workspace_id=workspace_id,
                document_id=uuid4(),
                filename=f"f{i}.md",
                mime="text/markdown",
                size_bytes=10,
                content_sha256=content_hash,
                object_key=object_key,
            )
        )

    first_page = await ListDocuments(documents=documents).execute(
        ListDocumentsCommand(workspace_id=workspace_id, after=None, limit=3)
    )
    assert [d.filename for d in first_page] == ["f4.md", "f3.md", "f2.md"]

    second_page = await ListDocuments(documents=documents).execute(
        ListDocumentsCommand(
            workspace_id=workspace_id,
            after=(first_page[-1].created_at, first_page[-1].id),
            limit=3,
        )
    )
    assert [d.filename for d in second_page] == ["f1.md", "f0.md"]


async def test_delete_document_makes_it_invisible_and_enqueues_document_deleted() -> None:
    workspace_id, document_id = uuid4(), uuid4()
    object_key = f"{workspace_id}/{_CONTENT_HASH}"
    storage = FakeDocumentObjectStorage(existing_keys={object_key})
    documents = FakeDocumentRepository()
    outbox = FakeOutboxRepository()
    ids = FakeIdGenerator()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    await _confirm(
        documents=documents,
        storage=storage,
        outbox=outbox,
        ids=ids,
        workspace_id=workspace_id,
        document_id=document_id,
        object_key=object_key,
    )

    await DeleteDocument(documents=documents, outbox=outbox, clock=clock, ids=ids).execute(
        DeleteDocumentCommand(workspace_id=workspace_id, document_id=document_id)
    )

    with pytest.raises(DocumentNotFoundError):
        await GetDocument(documents=documents).execute(
            GetDocumentCommand(workspace_id=workspace_id, document_id=document_id)
        )
    pending = await outbox.fetch_pending(
        event_type=DOCUMENT_DELETED_EVENT_TYPE, max_attempts=999, limit=10
    )
    assert len(pending) == 1
    assert pending[0].payload == {"document_id": str(document_id)}


async def test_delete_document_raises_not_found_for_unknown_id() -> None:
    """Deliberately not idempotent (unlike DeleteThread) — FR-KB-5's
    "provable deletion" framing means silently no-op'ing an already-
    gone document would let a caller believe content was removed that
    was never there to remove."""
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(DocumentNotFoundError):
        await DeleteDocument(
            documents=FakeDocumentRepository(),
            outbox=FakeOutboxRepository(),
            clock=clock,
            ids=FakeIdGenerator(),
        ).execute(DeleteDocumentCommand(workspace_id=uuid4(), document_id=uuid4()))


async def test_delete_document_twice_raises_not_found_the_second_time() -> None:
    workspace_id, document_id = uuid4(), uuid4()
    object_key = f"{workspace_id}/{_CONTENT_HASH}"
    storage = FakeDocumentObjectStorage(existing_keys={object_key})
    documents = FakeDocumentRepository()
    outbox = FakeOutboxRepository()
    ids = FakeIdGenerator()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    await _confirm(
        documents=documents,
        storage=storage,
        outbox=outbox,
        ids=ids,
        workspace_id=workspace_id,
        document_id=document_id,
        object_key=object_key,
    )
    delete = DeleteDocument(documents=documents, outbox=outbox, clock=clock, ids=ids)
    await delete.execute(DeleteDocumentCommand(workspace_id=workspace_id, document_id=document_id))

    with pytest.raises(DocumentNotFoundError):
        await delete.execute(
            DeleteDocumentCommand(workspace_id=workspace_id, document_id=document_id)
        )
    # Only one document.deleted event, not two.
    pending = await outbox.fetch_pending(
        event_type=DOCUMENT_DELETED_EVENT_TYPE, max_attempts=999, limit=10
    )
    assert len(pending) == 1
