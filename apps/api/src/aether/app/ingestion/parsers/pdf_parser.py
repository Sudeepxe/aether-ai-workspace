"""PDF parser (§3.2.7, FR-KB-1). PDF has no reliably-recoverable
semantic structure in the general case — unlike DOCX/HTML/MD, most PDFs
carry no machine-readable heading/section tags, only visual layout.
Rather than a heuristic (font-size guessing, ALL-CAPS detection) that
would silently mistag content with false confidence, this parser is
honest about the limitation: paragraphs only, no heading nodes, each
tagged with its real source page — genuine, provenance-correct
information, not fabricated structure.
"""

from __future__ import annotations

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from aether.app.ingestion.document_tree import DocumentNode, NodeKind


class PdfParseError(Exception):
    """The PDF is malformed, encrypted, or otherwise unreadable — a
    stage-specific failure surfaced to the user (§3.2.7's "failed at
    parsing: encrypted PDF")."""


def parse_pdf(content: bytes) -> list[DocumentNode]:
    try:
        reader = PdfReader(io.BytesIO(content))
    except PdfReadError as exc:
        raise PdfParseError(str(exc)) from exc

    if reader.is_encrypted:
        raise PdfParseError("PDF is password-protected/encrypted")

    nodes: list[DocumentNode] = []
    try:
        for page_index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            for paragraph in _split_paragraphs(text):
                nodes.append(
                    DocumentNode(
                        kind=NodeKind.PARAGRAPH, text=paragraph, level=None, page=page_index
                    )
                )
    except PdfReadError as exc:
        raise PdfParseError(str(exc)) from exc

    if not nodes:
        raise PdfParseError("no extractable text (possibly a scanned/image-only PDF)")
    return nodes


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]
