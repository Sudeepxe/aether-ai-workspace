from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.ingestion.process_document import DocumentProcessor
from aether.domain.entities import DocumentStatus
from aether.ports.ingestion_queue import QueuedMessage
from tests.unit.fakes.ingestion import FakeIngestionRepository, FakeObjectStorage, FakeScanner

pytestmark = pytest.mark.unit


def _message(*, document_id: str, object_key: str, mime: str) -> QueuedMessage:
    return QueuedMessage(
        stream_message_id="1-0",
        tenant_id=uuid4(),
        payload={"document_id": document_id, "object_key": object_key, "mime": mime},
        delivery_count=1,
    )


async def test_happy_path_processes_a_document_through_to_embedding_stage() -> None:
    document_id = str(uuid4())
    content = b"# Title\n\nSome real content to chunk.\n"
    storage = FakeObjectStorage(objects={"key1": content})
    scanner = FakeScanner()
    repository = FakeIngestionRepository()
    processor = DocumentProcessor(object_storage=storage, scanner=scanner, repository=repository)
    message = _message(document_id=document_id, object_key="key1", mime="text/markdown")

    await processor(message)

    assert repository.status_history == [
        DocumentStatus.SCANNING,
        DocumentStatus.PARSING,
        DocumentStatus.CHUNKING,
        DocumentStatus.EMBEDDING,
    ]
    assert repository.failures == []
    assert len(repository.inserted_chunks) >= 1
    assert "Title" in repository.inserted_chunks[0].content


async def test_malware_detected_marks_the_document_failed_at_scanning_stage() -> None:
    content = b"malicious payload"
    storage = FakeObjectStorage(objects={"key1": content})
    scanner = FakeScanner(malicious_keys={content})
    repository = FakeIngestionRepository()
    processor = DocumentProcessor(object_storage=storage, scanner=scanner, repository=repository)
    message = _message(document_id=str(uuid4()), object_key="key1", mime="text/plain")

    await processor(message)

    assert repository.status_history == [DocumentStatus.SCANNING]
    assert len(repository.failures) == 1
    assert repository.failures[0].stage == "scanning"
    assert "Fake-Test-Signature" in repository.failures[0].reason
    assert repository.inserted_chunks == []


async def test_unsupported_declared_type_marks_the_document_failed_at_parsing_stage() -> None:
    content = b"some plain content"
    storage = FakeObjectStorage(objects={"key1": content})
    scanner = FakeScanner()
    repository = FakeIngestionRepository()
    processor = DocumentProcessor(object_storage=storage, scanner=scanner, repository=repository)
    message = _message(document_id=str(uuid4()), object_key="key1", mime="application/x-nonsense")

    await processor(message)

    assert repository.status_history == [DocumentStatus.SCANNING, DocumentStatus.PARSING]
    assert len(repository.failures) == 1
    assert repository.failures[0].stage == "parsing"


async def test_corrupt_pdf_marks_the_document_failed_at_parsing_stage_not_retried_forever() -> None:
    """The poison-file case (§3.2.7's literal acceptance criterion):
    bounded handling with a stage-specific reason, not an unbounded
    retry loop — the handler catches this itself rather than letting it
    propagate to the queue's retry mechanism, since a corrupt PDF will
    fail identically on every attempt."""
    content = b"%PDF-1.4 this is not actually a valid pdf body at all"
    storage = FakeObjectStorage(objects={"key1": content})
    scanner = FakeScanner()
    repository = FakeIngestionRepository()
    processor = DocumentProcessor(object_storage=storage, scanner=scanner, repository=repository)
    message = _message(document_id=str(uuid4()), object_key="key1", mime="application/pdf")

    await processor(message)

    assert len(repository.failures) == 1
    assert repository.failures[0].stage == "parsing"
    assert repository.inserted_chunks == []


async def test_a_transient_storage_error_propagates_for_the_queues_own_retry_mechanism() -> None:
    """Infrastructure failures (storage unreachable) are NOT caught
    here — they must propagate so run_ingestion_consumer's fail() path
    (issue #45) handles bounded retry-then-DLQ, since these might
    genuinely succeed on a later attempt."""
    storage = FakeObjectStorage(objects={})  # key1 deliberately missing -> KeyError
    scanner = FakeScanner()
    repository = FakeIngestionRepository()
    processor = DocumentProcessor(object_storage=storage, scanner=scanner, repository=repository)
    message = _message(document_id=str(uuid4()), object_key="key1", mime="text/plain")

    with pytest.raises(KeyError):
        await processor(message)

    assert repository.failures == []  # not treated as a permanent failure
