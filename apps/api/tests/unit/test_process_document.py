from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from aether.app.ingestion.process_document import DocumentProcessor
from aether.domain.entities import DocumentStatus
from aether.ports.embedding import EmbeddingProviderError
from aether.ports.ingestion_queue import QueuedMessage
from tests.unit.fakes.ingestion import (
    FakeEmbedder,
    FakeIngestionRepository,
    FakeObjectStorage,
    FakeScanner,
)

pytestmark = pytest.mark.unit


def _message(*, document_id: str, object_key: str, mime: str) -> QueuedMessage:
    return QueuedMessage(
        stream_message_id="1-0",
        tenant_id=uuid4(),
        payload={"document_id": document_id, "object_key": object_key, "mime": mime},
        delivery_count=1,
    )


async def test_happy_path_processes_a_document_through_to_ready() -> None:
    document_id = str(uuid4())
    content = b"# Title\n\nSome real content to chunk.\n"
    storage = FakeObjectStorage(objects={"key1": content})
    scanner = FakeScanner()
    repository = FakeIngestionRepository()
    embedder = FakeEmbedder()
    message = _message(document_id=document_id, object_key="key1", mime="text/markdown")
    processor = DocumentProcessor(
        object_storage=storage, scanner=scanner, repository=repository, embedder=embedder
    )

    await processor(message)

    assert repository.status_history == [
        DocumentStatus.SCANNING,
        DocumentStatus.PARSING,
        DocumentStatus.CHUNKING,
        DocumentStatus.EMBEDDING,
        DocumentStatus.READY,
    ]
    assert repository.failures == []
    assert len(repository.inserted_chunks) >= 1
    assert "Title" in repository.inserted_chunks[0].content
    persisted = await repository.list_chunks(message.tenant_id, UUID(document_id))
    assert len(persisted) == len(repository.inserted_chunks)
    assert all(c.embedding is not None for c in persisted)
    assert all(c.embedding_model == "fake-embedder" for c in persisted)


async def test_duplicate_content_reuses_a_cached_embedding_instead_of_re_embedding() -> None:
    """The content-hash dedupe cache (§3.2.7's cost note): identical
    chunk content already embedded once (e.g. from a prior document)
    is reused, not re-sent to the embedding provider."""
    content = b"# Title\n\nRepeated shared content across two documents.\n"
    storage = FakeObjectStorage(objects={"key1": content, "key2": content})
    scanner = FakeScanner()
    repository = FakeIngestionRepository()
    embedder = FakeEmbedder()
    processor = DocumentProcessor(
        object_storage=storage, scanner=scanner, repository=repository, embedder=embedder
    )

    await processor(_message(document_id=str(uuid4()), object_key="key1", mime="text/markdown"))
    calls_after_first = len(embedder.calls)
    assert calls_after_first > 0

    await processor(_message(document_id=str(uuid4()), object_key="key2", mime="text/markdown"))

    # The second document's identical chunks were all cache hits — no
    # new embed_batch call happened (an empty `to_embed` list short-
    # circuits before ever calling the embedder).
    assert len(embedder.calls) == calls_after_first
    assert repository.status_history[-1] == DocumentStatus.READY


async def test_redelivery_after_success_is_idempotent_and_reuses_existing_embeddings() -> None:
    document_id = str(uuid4())
    content = b"# Title\n\nContent for a redelivery idempotency check.\n"
    storage = FakeObjectStorage(objects={"key1": content})
    scanner = FakeScanner()
    repository = FakeIngestionRepository()
    embedder = FakeEmbedder()
    processor = DocumentProcessor(
        object_storage=storage, scanner=scanner, repository=repository, embedder=embedder
    )
    message = _message(document_id=document_id, object_key="key1", mime="text/markdown")

    await processor(message)
    calls_after_first = len(embedder.calls)
    await processor(message)  # simulated at-least-once redelivery

    assert len(embedder.calls) == calls_after_first  # nothing new needed embedding
    assert repository.status_history.count(DocumentStatus.READY) == 2


async def test_a_retryable_embedding_error_propagates_for_the_queues_own_retry_mechanism() -> None:
    """Embedding quota exhaustion (§3.2.7's failure-scenario note) is
    NOT caught here — it must propagate so issue #45's retry-then-DLQ
    and per-tenant fair queuing absorb it, since a later attempt might
    genuinely succeed once the provider's quota recovers."""
    content = b"# Title\n\nSome content.\n"
    storage = FakeObjectStorage(objects={"key1": content})
    scanner = FakeScanner()
    repository = FakeIngestionRepository()
    embedder = FakeEmbedder(error=EmbeddingProviderError("rate limited", retryable=True))
    processor = DocumentProcessor(
        object_storage=storage, scanner=scanner, repository=repository, embedder=embedder
    )
    message = _message(document_id=str(uuid4()), object_key="key1", mime="text/markdown")

    with pytest.raises(EmbeddingProviderError):
        await processor(message)

    assert repository.failures == []  # not treated as a permanent failure
    assert DocumentStatus.READY not in repository.status_history


async def test_a_non_retryable_embedding_error_marks_the_document_failed_at_embedding_stage() -> (
    None
):
    content = b"# Title\n\nSome content.\n"
    storage = FakeObjectStorage(objects={"key1": content})
    scanner = FakeScanner()
    repository = FakeIngestionRepository()
    embedder = FakeEmbedder(error=EmbeddingProviderError("invalid request", retryable=False))
    processor = DocumentProcessor(
        object_storage=storage, scanner=scanner, repository=repository, embedder=embedder
    )
    message = _message(document_id=str(uuid4()), object_key="key1", mime="text/markdown")

    await processor(message)

    assert len(repository.failures) == 1
    assert repository.failures[0].stage == "embedding"
    assert "invalid request" in repository.failures[0].reason
    assert DocumentStatus.READY not in repository.status_history


async def test_malware_detected_marks_the_document_failed_at_scanning_stage() -> None:
    content = b"malicious payload"
    storage = FakeObjectStorage(objects={"key1": content})
    scanner = FakeScanner(malicious_keys={content})
    repository = FakeIngestionRepository()
    embedder = FakeEmbedder()
    processor = DocumentProcessor(
        object_storage=storage, scanner=scanner, repository=repository, embedder=embedder
    )
    message = _message(document_id=str(uuid4()), object_key="key1", mime="text/plain")

    await processor(message)

    assert repository.status_history == [DocumentStatus.SCANNING]
    assert len(repository.failures) == 1
    assert repository.failures[0].stage == "scanning"
    assert "Fake-Test-Signature" in repository.failures[0].reason
    assert repository.inserted_chunks == []
    assert embedder.calls == []


async def test_unsupported_declared_type_marks_the_document_failed_at_parsing_stage() -> None:
    content = b"some plain content"
    storage = FakeObjectStorage(objects={"key1": content})
    scanner = FakeScanner()
    repository = FakeIngestionRepository()
    embedder = FakeEmbedder()
    processor = DocumentProcessor(
        object_storage=storage, scanner=scanner, repository=repository, embedder=embedder
    )
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
    embedder = FakeEmbedder()
    processor = DocumentProcessor(
        object_storage=storage, scanner=scanner, repository=repository, embedder=embedder
    )
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
    embedder = FakeEmbedder()
    processor = DocumentProcessor(
        object_storage=storage, scanner=scanner, repository=repository, embedder=embedder
    )
    message = _message(document_id=str(uuid4()), object_key="key1", mime="text/plain")

    with pytest.raises(KeyError):
        await processor(message)

    assert repository.failures == []  # not treated as a permanent failure
