"""Plain-text parser (§3.2.7, FR-KB-1). No structure to recover — every
blank-line-separated block becomes one paragraph node, no headings.
"""

from __future__ import annotations

from aether.app.ingestion.document_tree import DocumentNode, NodeKind


class TextParseError(Exception):
    """The content decoded but contained no non-whitespace text."""


def parse_text(content: bytes) -> list[DocumentNode]:
    text = content.decode("utf-8")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        raise TextParseError("no extractable content (empty document)")
    return [
        DocumentNode(kind=NodeKind.PARAGRAPH, text=p, level=None, page=None) for p in paragraphs
    ]
