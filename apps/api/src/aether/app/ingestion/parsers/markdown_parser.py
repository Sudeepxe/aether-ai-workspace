"""Markdown parser (§3.2.7, FR-KB-1). CommonMark via markdown-it-py —
headings (``#``..``######``) map to real heading levels; everything
else (paragraphs, lists, blockquotes) is treated as paragraph text.
Strict CommonMark has no table syntax, so tables aren't specially
recognized here — GFM-style pipe tables render as ordinary paragraph
text, a documented, honest scope limit rather than a silent mis-parse.
"""

from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token

from aether.app.ingestion.document_tree import DocumentNode, NodeKind

_md = MarkdownIt("commonmark")


class MarkdownParseError(Exception):
    """The content decoded as text but produced no parseable structure
    at all (e.g. empty after stripping)."""


def parse_markdown(content: bytes) -> list[DocumentNode]:
    text = content.decode("utf-8")
    tokens = _md.parse(text)

    nodes: list[DocumentNode] = []
    pending_heading_level: int | None = None
    for token in tokens:
        if token.type == "heading_open":
            pending_heading_level = int(token.tag.removeprefix("h"))
        elif token.type == "inline":
            plain_text = _plain_text(token).strip()
            if not plain_text:
                continue
            if pending_heading_level is not None:
                nodes.append(
                    DocumentNode(
                        kind=NodeKind.HEADING,
                        text=plain_text,
                        level=pending_heading_level,
                        page=None,
                    )
                )
                pending_heading_level = None
            else:
                nodes.append(
                    DocumentNode(kind=NodeKind.PARAGRAPH, text=plain_text, level=None, page=None)
                )

    if not nodes:
        raise MarkdownParseError("no extractable content (empty document)")
    return nodes


def _plain_text(inline_token: Token) -> str:
    if not inline_token.children:
        return inline_token.content
    return "".join(child.content for child in inline_token.children)
