"""A deterministic, non-semantic embedding fallback — mirrors
``adapters/echo/generator.py``'s role for chat completions (§3.2.4,
issue #38): dev/CI environments without a SOPS-decrypted
``AETHER_OPENAI_API_KEY`` still get a real, honest, working embedder
rather than a silent stub, so the ingestion pipeline is exercisable
end-to-end (queued -> ... -> ready) without a live provider key.

The vectors carry no semantic meaning whatsoever — retrieval quality
against them is meaningless (each text's vector is a hash expansion of
its own bytes, unrelated to any other text's meaning). What they DO
guarantee, deterministically: identical content always produces the
identical vector (so the content-hash dedupe cache and idempotent
re-embedding tests exercise real logic, not random noise) and distinct
content produces distinct vectors, at the real ``vector(1536)`` column's
exact dimensionality.
"""

from __future__ import annotations

import hashlib
import struct

_DIMENSIONS = 1536


class LocalHashEmbeddingAdapter:
    model = "local-hash-fallback"
    embedding_version = 1

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vector(text) for text in texts]


def _hash_vector(text: str) -> list[float]:
    values: list[float] = []
    block = hashlib.sha256(text.encode()).digest()
    while len(values) < _DIMENSIONS:
        block = hashlib.sha256(block).digest()
        for offset in range(0, len(block), 4):
            if len(values) >= _DIMENSIONS:
                break
            (as_uint32,) = struct.unpack_from(">I", block, offset)
            values.append((as_uint32 / 0xFFFFFFFF) * 2 - 1)
    return values
