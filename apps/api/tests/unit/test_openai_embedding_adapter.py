from __future__ import annotations

import json

import httpx
import pytest

from aether.adapters.openai.embedding import OpenAiEmbeddingAdapter
from aether.ports.embedding import EmbeddingProviderError

pytestmark = pytest.mark.unit


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _embeddings_response(vectors: list[list[float]]) -> httpx.Response:
    # Deliberately returned out of request order — the adapter must
    # sort by each item's own `index` rather than trust response order.
    data = [
        {"embedding": vector, "index": i, "object": "embedding"} for i, vector in enumerate(vectors)
    ]
    return httpx.Response(
        200, json={"data": list(reversed(data)), "model": "text-embedding-3-small"}
    )


async def test_embed_batch_returns_vectors_in_request_order_even_if_response_is_shuffled() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _embeddings_response([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]])

    adapter = OpenAiEmbeddingAdapter(api_key="sk-test", client=_client(handler))
    vectors = await adapter.embed_batch(["a", "b", "c"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]


async def test_embed_batch_splits_large_input_into_sub_batches() -> None:
    captured_batches: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured_batches.append(len(body["input"]))
        return _embeddings_response([[0.0] for _ in body["input"]])

    adapter = OpenAiEmbeddingAdapter(api_key="sk-test", client=_client(handler))
    texts = [f"text-{i}" for i in range(250)]
    vectors = await adapter.embed_batch(texts)

    assert len(vectors) == 250
    assert captured_batches == [100, 100, 50]


async def test_request_body_uses_the_configured_model() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _embeddings_response([[0.0]])

    adapter = OpenAiEmbeddingAdapter(api_key="sk-test", client=_client(handler))
    await adapter.embed_batch(["hello"])

    assert captured["body"]["model"] == "text-embedding-3-small"
    assert captured["body"]["input"] == ["hello"]


async def test_empty_input_makes_no_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called for empty input")

    adapter = OpenAiEmbeddingAdapter(api_key="sk-test", client=_client(handler))
    assert await adapter.embed_batch([]) == []


async def test_5xx_response_raises_retryable_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"service unavailable")

    adapter = OpenAiEmbeddingAdapter(api_key="sk-test", client=_client(handler))
    with pytest.raises(EmbeddingProviderError) as exc_info:
        await adapter.embed_batch(["hi"])
    assert exc_info.value.retryable is True


async def test_429_response_raises_retryable_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"rate limited")

    adapter = OpenAiEmbeddingAdapter(api_key="sk-test", client=_client(handler))
    with pytest.raises(EmbeddingProviderError) as exc_info:
        await adapter.embed_batch(["hi"])
    assert exc_info.value.retryable is True


async def test_400_response_raises_non_retryable_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"invalid request")

    adapter = OpenAiEmbeddingAdapter(api_key="sk-test", client=_client(handler))
    with pytest.raises(EmbeddingProviderError) as exc_info:
        await adapter.embed_batch(["hi"])
    assert exc_info.value.retryable is False


async def test_transport_error_raises_retryable_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = OpenAiEmbeddingAdapter(api_key="sk-test", client=_client(handler))
    with pytest.raises(EmbeddingProviderError) as exc_info:
        await adapter.embed_batch(["hi"])
    assert exc_info.value.retryable is True
