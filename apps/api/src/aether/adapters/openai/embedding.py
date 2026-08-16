"""OpenAI EmbeddingProviderPort implementation (§3.2.7, §6.2/D6-3).
Raw httpx against the REST API, matching ``completion.py``'s "thin
in-house, avoid dependency churn" approach (ADR-3.5) rather than pulling
in the official SDK for one endpoint.

``text-embedding-3-small`` (1536d) is the hosted default per D6-3,
matching the ``chunks.embedding vector(1536)`` column exactly.
"""

from __future__ import annotations

import httpx

from aether.ports.embedding import EmbeddingProviderError

_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"

# Sub-batch cap per HTTP call — OpenAI's real limit on the embeddings
# endpoint's `input` array is far higher (2048), but keeping requests
# smaller bounds payload size and per-call blast radius, and genuinely
# exercises "batched" behavior (§3.2.7) for realistic per-document
# chunk counts rather than always firing exactly one request.
_MAX_REQUEST_BATCH = 100


class OpenAiEmbeddingAdapter:
    model = "text-embedding-3-small"
    embedding_version = 1

    def __init__(self, *, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        # Accepts an injected client so tests never make a real network
        # call — see tests/unit/test_openai_embedding_adapter.py.
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _MAX_REQUEST_BATCH):
            vectors.extend(await self._embed_one_request(texts[start : start + _MAX_REQUEST_BATCH]))
        return vectors

    async def _embed_one_request(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._client.post(
                _EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self.model, "input": texts},
            )
        except httpx.TransportError as exc:
            raise EmbeddingProviderError(f"openai transport error: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            raise EmbeddingProviderError(
                f"openai returned {response.status_code}: {response.text}",
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        # The API's own docs promise data is returned in request order,
        # but sorting by the response's own `index` field is a cheap,
        # explicit guarantee rather than a trusted assumption.
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in data]
