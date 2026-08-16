from __future__ import annotations

from uuid import UUID

from aether.ports.retrieval import ChunkSearchResult


class FakeChunkSearch:
    def __init__(
        self,
        *,
        vector_results: list[ChunkSearchResult] | None = None,
        lexical_results: list[ChunkSearchResult] | None = None,
        vector_error: Exception | None = None,
    ) -> None:
        self._vector_results = vector_results or []
        self._lexical_results = lexical_results or []
        self._vector_error = vector_error
        self.vector_calls: list[tuple[UUID, list[float], int]] = []
        self.lexical_calls: list[tuple[UUID, str, int]] = []

    async def search_vector(
        self, workspace_id: UUID, *, embedding: list[float], limit: int
    ) -> list[ChunkSearchResult]:
        self.vector_calls.append((workspace_id, embedding, limit))
        if self._vector_error is not None:
            raise self._vector_error
        return self._vector_results[:limit]

    async def search_lexical(
        self, workspace_id: UUID, *, query: str, limit: int
    ) -> list[ChunkSearchResult]:
        self.lexical_calls.append((workspace_id, query, limit))
        return self._lexical_results[:limit]


class FakeQueryEmbedder:
    model = "fake-query-embedder"
    embedding_version = 1

    def __init__(self, *, vector: list[float] | None = None) -> None:
        self._vector = vector or [0.1] * 4

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vector for _ in texts]
